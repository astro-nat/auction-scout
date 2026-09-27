"""Fill in seller history for Vinted lots already on file.

The seller gate reads three facts about the account behind a listing -
ratings, closet size, whether they have ever bought anything - and those
are fetched during a scan. Lots imported before the gate existed have
none, so they keep the benefit of the doubt forever: a burner account's
gold badge stands until somebody rescans that keyword.

This is that rescan, without the scan. It looks each distinct seller up
once, writes the answer onto every lot of theirs, and re-grades those lots
on the spot - the grade is pure arithmetic over stored values, so nothing
here costs a comp lookup or an AI call. Only the badge can change.
"""

import logging

from sqlalchemy.orm import Session

from .. import models
from ..database import SessionLocal
from ..services import jobs, vinted
from .enrich import _apply_roi

logger = logging.getLogger(__name__)


def seller_ids_on_file(db: Session) -> list[str]:
    """Every distinct Vinted seller with a lot here, oldest lot first so a
    resumed run keeps its order."""
    rows = (db.query(models.Lot.seller_id)
              .filter(models.Lot.seller_id.isnot(None))
              .group_by(models.Lot.seller_id)
              .order_by(models.Lot.seller_id)
              .all())
    return [r[0] for r in rows]


def apply_to_lots(db: Session, seller_id: str, stats: dict) -> int:
    """Write one seller's history onto their lots and re-grade them."""
    if stats.get("item_count") is None:
        # The lookup told us nothing. Writing NULLs would be indistinguishable
        # from "never looked up", which is the state we are trying to leave.
        return 0
    rows = (db.query(models.Lot)
              .filter(models.Lot.seller_id == seller_id).all())
    for lot in rows:
        lot.seller_rating = stats.get("rating")
        lot.seller_feedback_count = stats.get("feedback_count")
        lot.seller_item_count = stats.get("item_count")
        lot.seller_bought_count = stats.get("bought_count")
        # The gate lives in _apply_roi, and it only runs when something
        # re-prices. Nothing is going to re-price these, so run it here:
        # pure arithmetic over values already stored, no network, no spend.
        if lot.enrichment is not None:
            _apply_roi(lot, lot.enrichment)
    return len(rows)


def run_seller_backfill(seller_ids: list[str],
                        resume_job_id: str | None = None) -> None:
    """Look up every seller in the list, one at a time."""
    db: Session = SessionLocal()
    if resume_job_id:
        job = resume_job_id
        start_at = (jobs.get(job) or {}).get("current") or 0
    else:
        job = jobs.start("backfill-sellers",
                         f"Looking up {len(seller_ids)} Vinted sellers",
                         total=len(seller_ids),
                         payload={"seller_ids": seller_ids})
        start_at = 0
    looked_up = touched = gated = 0
    try:
        for i, sid in enumerate(seller_ids[start_at:], start_at + 1):
            if jobs.is_cancelled(job):
                logger.info("Seller backfill cancelled after %d", i - 1)
                break
            jobs.update(job, current=i, detail=f"seller {sid}")
            try:
                stats = vinted.seller_stats(int(sid))
            except Exception as exc:  # noqa: BLE001 - one bad seller must not stop the run
                logger.warning("Seller lookup failed for %s: %s", sid, exc)
                continue
            n = apply_to_lots(db, sid, stats)
            if n:
                looked_up += 1
                touched += n
                db.commit()
        gated = (db.query(models.Enrichment)
                   .filter(models.Enrichment.roi_reason.like("seller has no ratings%"))
                   .count())
    finally:
        jobs.finish(job)
        db.close()
    logger.info("Seller backfill: %d sellers, %d lots updated, %d lots now gated",
                looked_up, touched, gated)
