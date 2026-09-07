"""GET /status — everything happening server-side right now, for the top bar."""

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import models
from ..database import get_db
from ..services import jobs

router = APIRouter(tags=["status"])


@router.get("/status")
def get_status(db: Session = Depends(get_db)):
    """Active scans/imports plus the enrichment queue's live state."""
    from sqlalchemy import case, func
    # One aggregate pass: total queued plus the enrich/inspect split, so the
    # bar can say what KIND of work is waiting, straight from postgres.
    row = (
        db.query(
            func.count(models.Enrichment.id),
            func.count(case((models.Enrichment.queued_task == "inspect", 1))),
            # Enriched in the last few minutes — a live "things are moving"
            # signal the bar can show as a rate.
        )
        .filter(models.Enrichment.status == "queued")
        .one()
    )
    queued, inspect_queued = row[0], row[1]
    from datetime import datetime, timedelta, timezone
    done_recently = (
        db.query(func.count(models.Enrichment.id))
        .filter(models.Enrichment.status.in_(["success", "failed"]),
                models.Enrichment.last_attempted_at
                >= datetime.now(timezone.utc) - timedelta(minutes=5))
        .scalar()
    )
    # The lot actually being worked right now publishes a stage string.
    working = (
        db.query(models.Enrichment, models.Lot.title)
        .join(models.Lot, models.Lot.id == models.Enrichment.lot_id)
        .filter(models.Enrichment.status == "queued",
                models.Enrichment.progress.isnot(None))
        .first()
    )
    enrichment = {"queued": queued, "stage": None, "lot_title": None,
                  "inspect_queued": inspect_queued,
                  "enrich_queued": queued - inspect_queued,
                  "done_last_5min": done_recently or 0}
    if working:
        enrichment["stage"] = working[0].progress
        enrichment["lot_title"] = working[1]

    return {"jobs": jobs.active(), "enrichment": enrichment}


@router.get("/settings")
def get_settings():
    """The tunables the UI can edit. target_roi_pct is served as a percent."""
    from ..services import financials
    return {"target_roi_pct": round(financials.current_target_roi() * 100)}


@router.patch("/settings")
def patch_settings(payload: dict,
                   background_tasks: BackgroundTasks,
                   db: Session = Depends(get_db)):
    """Save a new ROI target and immediately reprice every enriched lot under
    it (free — reuses stored AI results). 1-10000 sanity range."""
    from ..services import settings as settings_store
    from ..workers.enrich import run_reprice
    pct = payload.get("target_roi_pct")
    if not isinstance(pct, (int, float)) or not (1 <= pct <= 10000):
        raise HTTPException(status_code=422,
                            detail="target_roi_pct must be a number from 1 to 10000")
    settings_store.set("target_roi_pct", str(float(pct)))
    lot_ids = [row[0] for row in
               db.query(models.Lot.id).join(models.Enrichment)
                 .filter(models.Enrichment.enriched_title.isnot(None)).all()]
    if lot_ids:
        background_tasks.add_task(run_reprice, lot_ids)
    return {"target_roi_pct": pct, "repricing": len(lot_ids)}


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str):
    """Ask a running scan/import/reprice to stop. Work already saved stays."""
    return {"cancelled": jobs.cancel(job_id)}


@router.post("/enrichment/cancel")
def cancel_enrichment(db: Session = Depends(get_db)):
    """Drain the enrichment queue. Lots waiting their turn go back to
    'pending' (re-runnable); the one mid-flight finishes — stopping it
    halfway would burn the API call and save nothing."""
    n = (db.query(models.Enrichment)
           .filter(models.Enrichment.status == "queued")
           .update({"status": "pending", "progress": None},
                   synchronize_session=False))
    db.commit()
    return {"cancelled": n}
