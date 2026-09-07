"""Bid refresh — re-pull current bids for imported, still-open auctions.

Bids were only as fresh as the last import; this worker re-fetches each
imported auction's lots from HiBid and updates the bid/cost fields on rows
we already have (never creating new lots — that's Import's job), then
recomputes ROI for enriched lots so profit/verdict reflect the live bid.
Free: HiBid's API and pure math, no AI calls.

Follows the persisted-job pattern (see workers/enrich.py): the id list
rides the jobs row, `current` is the resume checkpoint, and
workers/resume.py restarts it after a deploy.
"""

import asyncio
import logging

from sqlalchemy.orm import Session

from ..database import SessionLocal
from .. import models
from ..services import hibid, jobs
from .enrich import _apply_roi

logger = logging.getLogger(__name__)


def run_bid_refresh(auction_ids: list[int], resume_job_id: str | None = None) -> None:
    db: Session = SessionLocal()
    if resume_job_id:
        job = resume_job_id
        row = jobs.get(job)
        start_at = (row or {}).get("current") or 0
    else:
        job = jobs.start("bid-refresh", "Refreshing current bids",
                         total=len(auction_ids),
                         payload={"auction_ids": auction_ids})
        start_at = 0
    updated_total = 0
    try:
        remaining = auction_ids[start_at:]
        for i, auction_id in enumerate(remaining, start_at + 1):
            if jobs.is_cancelled(job):
                print(f"Bid refresh cancelled after {i - 1} auctions")
                break
            auction = (db.query(models.Auction)
                         .filter(models.Auction.id == auction_id).first())
            if not auction or not auction.hibid_id:
                jobs.update(job, current=i)
                continue
            jobs.update(job, current=i, detail=(auction.name or "")[:40])
            try:
                ctx = {"premium_mult": auction.buyer_premium_mult,
                       "source": auction.source}

                async def _fetch():
                    return await hibid.fetch_lots(
                        auction.hibid_id, auction_ctx=ctx,
                        should_cancel=lambda: jobs.is_cancelled(job))

                fresh = asyncio.run(_fetch())
            except Exception as exc:  # noqa: BLE001 — one bad auction must not stop the run
                logger.warning("Bid refresh fetch failed for auction %s: %s",
                               auction_id, exc)
                continue

            by_lot_id = {str(f["lot_id"]): f for f in fresh}
            rows = (db.query(models.Lot)
                      .filter(models.Lot.auction_id == auction_id).all())
            for lot in rows:
                data = by_lot_id.get(str(lot.lot_id))
                if not data:
                    # fetch_lots returns every still-OPEN lot; one of ours
                    # missing from it has individually closed (HiBid soft-
                    # closes catalogs progressively) — record that, or its
                    # status reads OPEN forever.
                    if (lot.status or "").upper() in ("OPEN", "POSTED"):
                        lot.status = "CLOSED"
                        lot.time_left = None
                    continue
                for k in ("current_bid", "next_bid", "bid_count", "est_cost",
                          "status", "time_left", "closes_at"):
                    setattr(lot, k, data[k])
                # New bid moves cost, so the ROI verdict has to move with it.
                if lot.enrichment and lot.enrichment.est_resale:
                    _apply_roi(lot, lot.enrichment)
                updated_total += 1
            db.commit()
    finally:
        jobs.finish(job)
        db.close()
    print(f"Bid refresh complete: {updated_total} lots updated")
