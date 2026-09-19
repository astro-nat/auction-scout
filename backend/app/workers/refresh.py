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
import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from ..database import SessionLocal
from .. import config, models
from ..services import hibid, jobs
from .enrich import _apply_roi

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    """Naive UTC, matching how closing_date and closes_at are stored.

    Explicit because the mismatch has bitten before: these columns hold UTC
    with no tzinfo, and a bare datetime.now() is local time — the two only
    agree because the containers run UTC.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _lot_num(value) -> int | None:
    """Numeric prefix of a catalog number ('214A' → 214), else None."""
    m = re.match(r"\s*(\d+)", str(value or ""))
    return int(m.group(1)) if m else None


_HAMMERED = ("CLOSED", "SOLD", "ENDED", "PASSED", "ARCHIVED")


def live_hammered_through(fresh: list[dict]) -> int | None:
    """How far a LIVE (webcast) sale has progressed, or None.

    Webcast lots carry no per-lot close time — that's the gate: any
    closes_at in the fetch means a timed sale, where lots close on their
    own clocks and this inference would be wrong. A webcast crier works
    the catalog in order, so the HIGHEST hammered catalog number marks the
    sale's progress: anything numerically below it that still reads OPEN
    was passed or never updated, and can't be bid on anymore."""
    if not fresh or any(f.get("closes_at") for f in fresh):
        return None
    done = [n for f in fresh
            if (f.get("status") or "").upper() in _HAMMERED
            and (n := _lot_num(f.get("lot_number"))) is not None]
    return max(done) if done else None


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
            hammered_through = live_hammered_through(fresh)
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
                          "status", "time_left", "closes_at", "lot_number",
                          "estimate_low", "estimate_high"):
                    setattr(lot, k, data[k])
                # A live sale works the catalog in order: anything below the
                # furthest hammered lot is done, even if HiBid still says
                # open (passed lots keep an OPEN status forever).
                n = _lot_num(lot.lot_number)
                if (hammered_through is not None and n is not None
                        and n < hammered_through
                        and (lot.status or "").upper() in ("OPEN", "POSTED")):
                    lot.status = "CLOSED"
                    lot.time_left = None
                # New bid moves cost, so the ROI verdict has to move with it.
                if lot.enrichment and lot.enrichment.est_resale:
                    _apply_roi(lot, lot.enrichment)
                updated_total += 1
            db.commit()
    finally:
        jobs.finish(job)
        db.close()
    print(f"Bid refresh complete: {updated_total} lots updated")


def live_webcast_auction_ids(db: Session) -> list[int]:
    """Imported webcast auctions inside their live window.

    Webcast signature: the auction has imported lots and NONE of them carry
    a per-lot close time. closing_date is the posted END of the sale (HiBid
    lists when bidding closes, not when the crier starts), so a webcast is
    live in the LIVE_SALE_HOURS leading up to it — the first version had
    this backwards and never considered a running sale live."""
    now = _utcnow()
    has_timed = (db.query(models.Lot.auction_id)
                   .filter(models.Lot.auction_id.isnot(None),
                           models.Lot.closes_at.isnot(None))
                   .distinct())
    imported = (db.query(models.Lot.auction_id)
                  .filter(models.Lot.auction_id.isnot(None)).distinct())
    q = (db.query(models.Auction.id)
           .filter(models.Auction.hibid_id.isnot(None),
                   models.Auction.id.in_(imported),
                   models.Auction.id.notin_(has_timed),
                   models.Auction.closing_date.isnot(None),
                   models.Auction.closing_date >= now - timedelta(hours=1),
                   models.Auction.closing_date
                   <= now + timedelta(hours=config.LIVE_SALE_HOURS)))
    return [row[0] for row in q.all()]


def auctions_due_for_bid_refresh(db: Session,
                                 window_hours: float | None = None) -> list[int]:
    """Imported auctions with lots closing inside the window.

    Shared by the hourly loop and the manual endpoint, which had drifted into
    two copies of the same query.

    A lot's own `closes_at` decides when it is known — HiBid staggers
    closings, so an auction stays open for days while its lots finish in
    waves, and the auction-level date says nothing about the lot you care
    about. Where it is unknown (webcast sales carry no per-lot countdown —
    600 of the 1,304 lots on file right now) the auction's closing date
    stands in, and where neither is known the auction is included: we cannot
    rule it out, and silently never refreshing is the worse failure.
    """
    window = (config.BID_REFRESH_WINDOW_HOURS if window_hours is None
              else window_hours)
    imported = (db.query(models.Lot.auction_id)
                  .filter(models.Lot.auction_id.isnot(None)).distinct())
    q = (db.query(models.Auction.id)
           .filter(models.Auction.id.in_(imported),
                   models.Auction.hibid_id.isnot(None))
           .filter(or_(models.Auction.closing_date.is_(None),
                       models.Auction.closing_date >= _utcnow())))
    if window and window > 0:
        cutoff = _utcnow() + timedelta(hours=window)
        closing_soon = (
            db.query(models.Lot.auction_id)
              .filter(models.Lot.auction_id.isnot(None),
                      models.Lot.closes_at.isnot(None),
                      models.Lot.closes_at <= cutoff,
                      models.Lot.closes_at >= _utcnow())
              .distinct())
        # No per-lot times at all for this auction? Fall back to its own date.
        has_lot_times = (
            db.query(models.Lot.auction_id)
              .filter(models.Lot.auction_id.isnot(None),
                      models.Lot.closes_at.isnot(None))
              .distinct())
        q = q.filter(or_(
            models.Auction.id.in_(closing_soon),
            and_(models.Auction.id.notin_(has_lot_times),
                 or_(models.Auction.closing_date.is_(None),
                     models.Auction.closing_date <= cutoff)),
        ))
    return [row[0] for row in q.all()]
