"""GET /status — everything happening server-side right now, for the top bar."""

from fastapi import APIRouter, Depends, HTTPException
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

    # Worker liveness. With the background work in its own process, a dead
    # worker produces no errors at all — jobs just sit at 'pending' looking
    # like they're about to start. Saying so is the difference between
    # "still going" and "nothing is running this".
    workers = jobs.live_workers()
    return {"jobs": jobs.active(), "enrichment": enrichment,
            "workers": {"live": len(workers), "ids": [w["id"] for w in workers]}}


@router.get("/settings")
def get_settings():
    """The tunables the UI can edit. target_roi_pct is served as a percent.
    The pacing knobs (weekly goal, per-auction floor) live in the settings
    service — the closing-digest notifier reads the floor too."""
    from ..services import financials
    from ..services import settings as settings_store
    return {"target_roi_pct": round(financials.current_target_roi() * 100),
            "weekly_goal_usd": settings_store.money(
                "weekly_goal_usd", settings_store.WEEKLY_GOAL_DEFAULT),
            "auction_floor_usd": settings_store.money(
                "auction_floor_usd", settings_store.AUCTION_FLOOR_DEFAULT)}


@router.patch("/settings")
def patch_settings(payload: dict,
                   db: Session = Depends(get_db)):
    """Save any of the tunables. A new ROI target immediately re-grades every
    enriched lot (free — reuses stored AI results); the pacing numbers are
    display-only, so saving them re-grades nothing. Each field optional."""
    from ..services import settings as settings_store
    # Validate the WHOLE payload before writing any of it — a mixed request
    # with one bad field must not half-save (the 422 would read as "nothing
    # happened" while the valid half quietly stuck).
    to_save = {}
    for key, low, high in (("weekly_goal_usd", 0, 100000),
                           ("auction_floor_usd", 0, 100000)):
        if key in payload:
            v = payload[key]
            if not isinstance(v, (int, float)) or not (low <= v <= high):
                raise HTTPException(status_code=422,
                                    detail=f"{key} must be a number from {low} to {high}")
            to_save[key] = v
    roi = None
    if "target_roi_pct" in payload or not to_save:
        roi = payload.get("target_roi_pct")
        if not isinstance(roi, (int, float)) or not (1 <= roi <= 10000):
            raise HTTPException(status_code=422,
                                detail="target_roi_pct must be a number from 1 to 10000")

    for key, v in to_save.items():
        settings_store.set(key, str(float(v)))
    n = 0
    if roi is not None:
        settings_store.set("target_roi_pct", str(float(roi)))
        to_save["target_roi_pct"] = roi
        # Re-GRADE, not re-price: the ROI target doesn't change what anything
        # is worth, so this is arithmetic over stored values — no comp lookups.
        n = (db.query(models.Enrichment)
               .filter(models.Enrichment.est_resale.isnot(None)).count())
        if n:
            jobs.enqueue("regrade", "Re-grading items at the new ROI target",
                         total=n)
    return {**to_save, "regrading": n}


def week_start_utc(now):
    """Start of `now`'s acquisition week: Monday ~midnight Central, expressed
    in the naive UTC the database stores (05:00 UTC).

    A plain UTC Monday would reset the tracker on Sunday evening for the
    user; anchoring five hours back keeps Sunday-night bidding in the week
    it feels like it belongs to. Pure so the boundary is testable at fixed
    datetimes — DST drifts the true midnight an hour, which is accepted:
    the reset lands between midnight and 1am rather than mid-evening.
    """
    from datetime import timedelta
    anchor = now - timedelta(hours=5)
    start = (anchor - timedelta(days=anchor.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0)
    return start + timedelta(hours=5)


@router.get("/stats/week")
def week_stats(db: Session = Depends(get_db)):
    """Acquisition pacing: trusted profit won so far this week vs the goal.

    "Trusted" mirrors the settlement reconciliations: a won lot counts at its
    stored profit unless the AI audit demoted its value, and losses count
    against the week — the same arithmetic that priced the Sterling haul at
    $223 guaranteed. Wins enter via the 🏆 mark (won_at), so the number is
    only as complete as the marking; the available figure needs no marking
    at all, it is the summed profit of live GOLD MINE lots.
    """
    from datetime import datetime
    from sqlalchemy import func
    now = datetime.now()
    week_start = week_start_utc(now)

    rows = (db.query(models.Enrichment.profit, models.Enrichment.gold_check)
              .join(models.Lot, models.Lot.id == models.Enrichment.lot_id)
              .filter(models.Lot.won.is_(True),
                      models.Lot.won_at >= week_start,
                      models.Enrichment.profit.isnot(None))
              .all())
    won_profit = float(sum(p for p, check in rows if check != "demoted"))

    # What's on the table right now, across open auctions: profit of lots the
    # grader still calls GOLD MINE (post-audit, so evidence-gated).
    available = float(
        db.query(func.coalesce(func.sum(models.Enrichment.profit), 0))
          .join(models.Lot, models.Lot.id == models.Enrichment.lot_id)
          .join(models.Auction, models.Lot.auction_id == models.Auction.id)
          .filter(models.Enrichment.roi_status == "GOLD MINE",
                  models.Auction.closing_date.isnot(None),
                  models.Auction.closing_date >= now)
          .scalar())

    from ..services import settings as settings_store
    return {"week_start": week_start.isoformat(),
            "won_trusted_profit": round(won_profit, 2),
            "won_count": len(rows),
            "available_gold_profit": round(available, 2),
            "weekly_goal_usd": settings_store.money(
                "weekly_goal_usd", settings_store.WEEKLY_GOAL_DEFAULT),
            "auction_floor_usd": settings_store.money(
                "auction_floor_usd", settings_store.AUCTION_FLOOR_DEFAULT)}


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
