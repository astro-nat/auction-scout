"""GET /status — everything happening server-side right now, for the top bar."""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import models
from ..database import get_db
from ..services import jobs, pricing

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

    # Worker liveness. With the background work in its own process, a dead
    # worker produces no errors at all — jobs just sit at 'pending' looking
    # like they're about to start. Saying so is the difference between
    # "still going" and "nothing is running this".
    workers = jobs.live_workers()
    return {"jobs": jobs.active(), "enrichment": enrichment,
            "workers": {"live": len(workers), "ids": [w["id"] for w in workers]},
            # The last few SoldComps replies. A 200 with zero items logs
            # nothing, so this is the only place an "empty outage" shows.
            "soldcomps": pricing.soldcomps_recent()}


@router.get("/timings")
def get_timings(kind: str | None = None, hours: int = 24, limit: int = 200,
                db: Session = Depends(get_db)):
    """Recent phase timings, newest first, with a per-kind summary.

    Stored in Postgres rather than read from the log: Railway keeps a
    shallow buffer and returns a few dozen lines at a time, which is no use
    for a job that ran for twenty minutes.
    """
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    q = (db.query(models.TaskTiming)
           .filter(models.TaskTiming.started_at >= since)
           .order_by(models.TaskTiming.started_at.desc()))
    if kind:
        q = q.filter(models.TaskTiming.kind == kind)
    rows = q.limit(min(limit, 1000)).all()

    # Summary over the SAME window, not just the rows returned, so a busy
    # run does not make the averages depend on the page size.
    agg = (db.query(models.TaskTiming.kind, models.TaskTiming.phase,
                    func.count(models.TaskTiming.id),
                    func.sum(models.TaskTiming.duration_ms),
                    func.sum(models.TaskTiming.items),
                    func.avg(models.TaskTiming.per_item_ms))
             .filter(models.TaskTiming.started_at >= since))
    if kind:
        agg = agg.filter(models.TaskTiming.kind == kind)
    agg = agg.group_by(models.TaskTiming.kind, models.TaskTiming.phase).all()

    return {
        "window_hours": hours,
        "summary": [
            {"kind": k, "phase": p, "runs": runs,
             "total_s": round((total or 0) / 1000, 1),
             "items": int(items or 0),
             "avg_per_item_ms": round(avg, 2) if avg is not None else None}
            for k, p, runs, total, items, avg in agg
        ],
        "recent": [
            {"kind": r.kind, "phase": r.phase, "label": r.label,
             "auction_id": r.auction_id, "job_id": r.job_id,
             "started_at": r.started_at.isoformat() if r.started_at else None,
             "duration_s": round(r.duration_ms / 1000, 2),
             "items": r.items, "per_item_ms": r.per_item_ms,
             "detail": r.detail}
            for r in rows
        ],
    }


@router.get("/prices/history/{lot_id}")
def price_history(lot_id: str, db: Session = Depends(get_db)):
    """Every resale number this lot has been given, newest first."""
    lot = db.query(models.Lot).filter(models.Lot.lot_id == lot_id).first()
    if not lot:
        raise HTTPException(status_code=404, detail="Lot not found")
    rows = (db.query(models.PriceObservation)
              .filter(models.PriceObservation.lot_id == lot.id)
              .order_by(models.PriceObservation.created_at.desc()).all())
    return {
        "lot_id": lot_id,
        "title": lot.title,
        "current": float(lot.enrichment.est_resale)
        if lot.enrichment and lot.enrichment.est_resale is not None else None,
        "observations": [
            {"value": float(r.value) if r.value is not None else None,
             "method": r.method, "evidence": r.evidence,
             "comp_count": r.comp_count, "chosen": r.chosen,
             "rejected": r.rejected, "note": r.note, "query": r.query,
             "price_source": r.price_source,
             "at": r.created_at.isoformat() if r.created_at else None}
            for r in rows
        ],
    }


@router.get("/prices/disagreements")
def price_disagreements(hours: int = 720, min_ratio: float = 2.0,
                        limit: int = 100, db: Session = Depends(get_db)):
    """Lots where two methods gave materially different numbers.

    This is the point of keeping the rejected values. A lot that comps said
    was worth $370 and the audit said was worth $80 is a 4.6x miss, and the
    QUERY that produced it is the thing worth reading - that is where the
    next filter comes from.
    """
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    rows = (db.query(models.PriceObservation)
              .filter(models.PriceObservation.created_at >= since,
                      models.PriceObservation.value.isnot(None))
              .order_by(models.PriceObservation.lot_id,
                        models.PriceObservation.created_at).all())

    by_lot: dict = {}
    for r in rows:
        by_lot.setdefault(r.lot_id, []).append(r)

    out = []
    for lot_id, obs in by_lot.items():
        values = [float(o.value) for o in obs if o.value and float(o.value) > 0]
        if len(values) < 2:
            continue
        lo, hi = min(values), max(values)
        if lo <= 0 or hi / lo < min_ratio:
            continue
        lot = db.query(models.Lot).filter(models.Lot.id == lot_id).first()
        out.append({
            "lot_id": lot.lot_id if lot else None,
            "title": (lot.title if lot else "")[:80],
            "ratio": round(hi / lo, 1),
            "low": lo, "high": hi,
            "observations": [
                {"value": float(o.value), "method": o.method,
                 "evidence": o.evidence, "comp_count": o.comp_count,
                 "rejected": o.rejected, "query": o.query,
                 "note": (o.note or "")[:160]}
                for o in obs
            ],
        })
    out.sort(key=lambda r: -r["ratio"])
    return {"window_hours": hours, "min_ratio": min_ratio,
            "count": len(out), "lots": out[:limit]}


@router.get("/prices/evidence-mix")
def evidence_mix(hours: int = 720, db: Session = Depends(get_db)):
    """How often each evidence tier is produced, and how often it is thrown
    out. A tier that is usually rejected is one to stop trusting."""
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    rows = (db.query(models.PriceObservation.evidence,
                     models.PriceObservation.rejected,
                     func.count(models.PriceObservation.id),
                     func.avg(models.PriceObservation.value))
              .filter(models.PriceObservation.created_at >= since)
              .group_by(models.PriceObservation.evidence,
                        models.PriceObservation.rejected).all())
    mix: dict = {}
    for evidence, rejected, n, avg in rows:
        slot = mix.setdefault(evidence or "unknown",
                              {"produced": 0, "rejected": 0, "avg_value": None})
        slot["produced"] += n
        if rejected:
            slot["rejected"] += n
        if avg is not None and not rejected:
            slot["avg_value"] = round(float(avg), 2)
    for slot in mix.values():
        slot["reject_rate"] = (round(slot["rejected"] / slot["produced"], 3)
                               if slot["produced"] else None)
    return {"window_hours": hours, "by_evidence": mix}


@router.get("/settings")
def get_settings():
    """The tunables the UI can edit. target_roi_pct is served as a percent."""
    from ..services import financials
    from ..services import settings as settings_store
    return {"target_roi_pct": round(financials.current_target_roi() * 100),
            "exclude_titled_vehicles": settings_store.flag(
                "exclude_titled_vehicles",
                settings_store.EXCLUDE_VEHICLES_DEFAULT)}


@router.patch("/settings")
def patch_settings(payload: dict,
                   db: Session = Depends(get_db)):
    """Save any of the tunables. A new ROI target immediately re-grades every
    enriched lot (free — reuses stored AI results). Each field optional."""
    from ..services import settings as settings_store
    # Validate the WHOLE payload before writing any of it — a mixed request
    # with one bad field must not half-save (the 422 would read as "nothing
    # happened" while the valid half quietly stuck).
    to_save = {}
    vehicles = None
    if "exclude_titled_vehicles" in payload:
        vehicles = payload["exclude_titled_vehicles"]
        if not isinstance(vehicles, bool):
            raise HTTPException(status_code=422,
                                detail="exclude_titled_vehicles must be true or false")
    roi = None
    if "target_roi_pct" in payload or (not to_save and vehicles is None):
        roi = payload.get("target_roi_pct")
        if not isinstance(roi, (int, float)) or not (1 <= roi <= 10000):
            raise HTTPException(status_code=422,
                                detail="target_roi_pct must be a number from 1 to 10000")

    for key, v in to_save.items():
        settings_store.set(key, str(float(v)))
    regrade = False
    if vehicles is not None:
        settings_store.set("exclude_titled_vehicles",
                           "true" if vehicles else "false")
        to_save["exclude_titled_vehicles"] = vehicles
        regrade = True   # the gate reshapes verdicts fleet-wide
    if roi is not None:
        settings_store.set("target_roi_pct", str(float(roi)))
        to_save["target_roi_pct"] = roi
        regrade = True
    n = 0
    if regrade:
        # Re-GRADE, not re-price: neither knob changes what anything is
        # worth, so this is arithmetic over stored values — no comp lookups.
        n = (db.query(models.Enrichment)
               .filter(models.Enrichment.est_resale.isnot(None)).count())
        if n:
            jobs.enqueue("regrade", "Re-grading items under the new rules",
                         total=n)
    return {**to_save, "regrading": n}


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
