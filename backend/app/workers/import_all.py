"""Bulk import — pull lots for MANY auctions in one background job.

A category scan that surfaces 20 auctions with a few antiques each used to
mean 20 Import clicks, each a separate request-scoped import. This is the
same import run per auction by the worker process, following the
persisted-job pattern (see workers/enrich.py): the id list and category ride
the jobs row, `current` is the resume checkpoint, and the reaper restarts it
after a crash. Single-auction Import stays request-scoped in
routers/auctions.py; both share save_lots() below.
"""

import asyncio
import logging
from datetime import datetime, timezone

import httpx
from sqlalchemy.orm import Session

from ..database import SessionLocal
from .. import models
from ..services import hibid, jobs

logger = logging.getLogger(__name__)


def save_lots(db: Session, auction: models.Auction, lots: list[dict], *,
              on_progress=None, should_cancel=None) -> tuple[int, int, bool]:
    """Idempotent upsert of fetched lot dicts into one auction.

    Bids/status/time-left always come fresh on rows we already have;
    analysis fields stay. Returns (created, updated, cancelled)."""
    created = updated = 0
    cancelled = False
    for i, data in enumerate(lots, 1):
        if i % 10 == 0 or i == len(lots):
            if should_cancel and should_cancel():
                cancelled = True
                break            # keep what's saved so far
            if on_progress:
                on_progress(i, (data.get("title") or "")[:45])
        row = db.query(models.Lot).filter(models.Lot.lot_id == data["lot_id"]).first()
        if row:
            for k in ("current_bid", "next_bid", "bid_count", "est_cost",
                      "status", "time_left", "closes_at", "lot_number",
                      "thumbnail_url", "hd_thumbnail_url", "fullsize_url"):
                setattr(row, k, data[k])
            updated += 1
        else:
            row = models.Lot(auction_id=auction.id, **data)
            db.add(row)
            db.flush()
            db.add(models.Enrichment(lot_id=row.id, status="pending"))
            created += 1
    auction.imported_at = datetime.now(timezone.utc)
    db.commit()
    return created, updated, cancelled


def run_import_all(auction_ids: list[int], resume_job_id: str | None = None,
                   category_id: int = -1) -> None:
    """Import every auction in the list, one at a time.

    category_id limits each import to one HiBid category (the "import all
    the antiques" case). It rides the job payload rather than an argument
    the dispatcher would have to know about, so a resume keeps it too.
    """
    db: Session = SessionLocal()
    if resume_job_id:
        job = resume_job_id
        row = jobs.get(job) or {}
        start_at = row.get("current") or 0
        category_id = (row.get("payload") or {}).get("category_id", category_id)
    else:
        job = jobs.start("import-all",
                         f"Importing lots from {len(auction_ids)} auctions",
                         total=len(auction_ids),
                         payload={"auction_ids": auction_ids,
                                  "category_id": category_id})
        start_at = 0
    imported = created_total = updated_total = 0
    try:
        remaining = auction_ids[start_at:]
        for i, auction_id in enumerate(remaining, start_at + 1):
            if jobs.is_cancelled(job):
                print(f"Import-all cancelled after {i - 1} auctions")
                break
            auction = (db.query(models.Auction)
                         .filter(models.Auction.id == auction_id).first())
            if not auction or not auction.hibid_id:
                jobs.update(job, current=i)
                continue
            name = (auction.name or "")[:40]
            jobs.update(job, current=i, label=f"Importing {name}")
            try:
                async def _fetch():
                    async with httpx.AsyncClient() as client:
                        meta = await hibid.fetch_auction_meta(client, [auction.hibid_id])
                    m = meta.get(auction.hibid_id, {})
                    if m.get("premium_mult"):
                        auction.buyer_premium_mult = m["premium_mult"]
                        auction.cond_ship = m.get("cond_ship", False)
                    ctx = {"premium_mult": auction.buyer_premium_mult,
                           "source": auction.source}
                    return await hibid.fetch_lots(
                        auction.hibid_id, auction_ctx=ctx, category_id=category_id,
                        # Detail isn't rendered, but update() heartbeats — so
                        # a slow 2,000-lot catalog can't look like a dead job.
                        on_progress=lambda fetched, total: jobs.update(
                            job, detail=f"{name} — {fetched}/{total} lots"),
                        should_cancel=lambda: jobs.is_cancelled(job))

                lots = asyncio.run(_fetch())
            except Exception as exc:  # noqa: BLE001 — one bad auction must not stop the run
                logger.warning("Import-all fetch failed for auction %s: %s",
                               auction_id, exc)
                continue
            c, u, _ = save_lots(db, auction, lots,
                                should_cancel=lambda: jobs.is_cancelled(job))
            created_total += c
            updated_total += u
            imported += 1
    finally:
        jobs.finish(job)
        db.close()
    print(f"Import-all complete: {imported} auctions, "
          f"{created_total} new lots, {updated_total} refreshed")
