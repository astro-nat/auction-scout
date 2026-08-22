"""Auction discovery + lot import.

POST /auctions/scan          — discover open HiBid auctions near a zip, upsert them
POST /auctions/{id}/import   — pull all open lots for one auction into Postgres
POST /auctions/{id}/enrich-all — queue enrichment for every un-enriched lot
GET  /auctions               — list what we know about
"""

from datetime import datetime, timezone  # noqa: F401 — datetime used in filters
from typing import List

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException

from .. import models, schemas
from ..database import get_db
from ..services import hibid, jobs
from ..workers.enrich import run_enrichment
from sqlalchemy.orm import Session

router = APIRouter(prefix="/auctions", tags=["auctions"])


@router.get("", response_model=List[schemas.AuctionOut])
def list_auctions(include_closed: bool = False, db: Session = Depends(get_db)):
    """Open auctions (closed ones stay in the DB but drop off the list),
    each annotated with its gold-mine tally: how many enriched lots are
    GOLD MINEs and their summed potential profit."""
    from sqlalchemy import func, or_
    q = db.query(models.Auction)
    if not include_closed:
        # Hide closed auctions — unless you imported lots from them, in which
        # case the card has to stay or your items look orphaned.
        imported = (db.query(models.Lot.auction_id)
                      .filter(models.Lot.auction_id.isnot(None))
                      .distinct())
        q = q.filter(or_(models.Auction.closing_date.is_(None),
                         models.Auction.closing_date >= datetime.now(),
                         models.Auction.id.in_(imported)))
    auctions = q.order_by(models.Auction.closing_date).all()
    return _attach_stats(db, auctions)


def _attach_stats(db: Session, auctions: list) -> list:
    """Annotate auction rows with gold-mine tallies and pipeline counts.

    Every endpoint that returns AuctionOut rows must go through here — a
    response with the schema's default zeros reads as "Not imported yet"
    in the UI even when the auction has a thousand lots in the database.
    """
    from sqlalchemy import case, func
    gold = dict()
    rows = (
        db.query(models.Lot.auction_id, func.count(models.Enrichment.id),
                 func.coalesce(func.sum(models.Enrichment.profit), 0))
        .join(models.Enrichment, models.Enrichment.lot_id == models.Lot.id)
        .filter(models.Enrichment.roi_status == "GOLD MINE")
        .group_by(models.Lot.auction_id)
        .all()
    )
    for auction_id, count, profit in rows:
        gold[auction_id] = (count, profit)

    # Per-auction pipeline state, so a card can say what's actually in the DB.
    stat_rows = (
        db.query(
            models.Lot.auction_id,
            func.count(models.Lot.id),
            func.count(case((models.Enrichment.status == "success", 1))),
            func.count(case((models.Enrichment.status.in_(["pending", "queued"]), 1))),
            func.count(case((models.Enrichment.status == "failed", 1))),
            func.count(case((models.Enrichment.ai_source == "vision-itemized", 1))),
            func.count(case(((models.Lot.logistics_ease == "HARD")
                             & models.Enrichment.status.in_(["pending", "failed"]), 1))),
        )
        .outerjoin(models.Enrichment, models.Enrichment.lot_id == models.Lot.id)
        .group_by(models.Lot.auction_id)
        .all()
    )
    stats = {r[0]: r[1:] for r in stat_rows}

    for a in auctions:
        a.gold_count, a.gold_profit = gold.get(a.id, (0, 0))
        (a.lots_imported, a.lots_enriched, a.lots_pending,
         a.lots_failed, a.lots_inspected,
         a.lots_hard_pending) = stats.get(a.id, (0, 0, 0, 0, 0, 0))
    return auctions


def purge_stale_auctions(db: Session) -> int:
    """Delete closed auctions we never imported any lots from.

    Every scan appends whatever HiBid returns, so the table grows without
    bound. An auction that has closed AND has no lots holds nothing worth
    keeping — no enrichment, no corrections, no history. Auctions with lots
    are always kept, closed or not.
    """
    stale_ids = [
        row[0] for row in
        db.query(models.Auction.id)
          .outerjoin(models.Lot, models.Lot.auction_id == models.Auction.id)
          .filter(models.Auction.closing_date.isnot(None),
                  models.Auction.closing_date < datetime.now(),
                  models.Lot.id.is_(None))
          .all()
    ]
    if stale_ids:
        (db.query(models.Auction)
           .filter(models.Auction.id.in_(stale_ids))
           .delete(synchronize_session=False))
        db.commit()
    return len(stale_ids)


@router.post("/purge-stale")
def purge_stale(db: Session = Depends(get_db)):
    """Manually drop closed auctions with no imported lots."""
    return {"removed": purge_stale_auctions(db)}


@router.get("/categories")
async def list_categories():
    """HiBid's top-level category tree, for the scan filter dropdown."""
    return await hibid.fetch_categories()


@router.post("/scan", response_model=List[schemas.AuctionOut])
async def scan_auctions(payload: schemas.ScanRequest, db: Session = Depends(get_db)):
    """Discover open auctions near the configured zip and store them."""
    # Keep the table from growing without bound — every scan appends.
    removed = purge_stale_auctions(db)
    if removed:
        print(f"Purged {removed} closed auctions with no imported lots")

    # A nationwide ("Anywhere") scan with no status limit would return every
    # open auction on HiBid — enforced here too, not just in the UI, so it
    # can't be bypassed by calling this endpoint directly.
    status = payload.status
    if payload.radius_miles == -1 and status not in ("CLOSING", "HOT"):
        status = "CLOSING"

    job = jobs.start("scan", "Scanning HiBid for auctions…")
    try:
        found = await hibid.discover_auctions(
            zip_code=payload.zip,
            radius_miles=payload.radius_miles,
            closing_within_days=payload.closing_within_days,
            include_nationwide=payload.include_nationwide,
            search_text=payload.search_text,
            category_id=payload.category_id,
            auction_type=payload.auction_type,
            status=status,
        )
    finally:
        jobs.finish(job)
    stored = []
    for a in found:
        row = db.query(models.Auction).filter(
            models.Auction.hibid_id == a["hibid_id"]).first()
        if row:
            for k, v in a.items():
                setattr(row, k, v)
        else:
            row = models.Auction(**a)
            db.add(row)
        stored.append(row)
    db.commit()

    # When scanning within a category, annotate each auction with how many of
    # its lots actually match — so the UI can offer "import just those".
    if payload.category_id and payload.category_id != -1:
        cjob = jobs.start("scan", f"Counting matching lots in {len(stored)} auctions…",
                          total=len(stored))
        try:
            counts = await hibid.count_matching_lots(
                [r.hibid_id for r in stored if r.hibid_id], payload.category_id)
        finally:
            jobs.finish(cjob)
        for r in stored:
            r.category_lot_count = counts.get(r.hibid_id)
            r.category_count_for = payload.category_id
        db.commit()   # persist so a page refresh keeps the "Import N" button
    # Attach the same stats GET /auctions serves — without this, previously
    # imported auctions come back with zeroed counts and the UI shows them
    # as "Not imported yet" until the next idle refresh.
    return _attach_stats(db, stored)


@router.post("/{auction_id}/import")
async def import_lots(auction_id: int, category_id: int = -1,
                      db: Session = Depends(get_db)):
    """Pull open lots for one auction into Postgres (idempotent upsert).
    category_id limits the import to one HiBid category server-side."""
    auction = db.query(models.Auction).filter(models.Auction.id == auction_id).first()
    if not auction or not auction.hibid_id:
        raise HTTPException(status_code=404, detail="Auction not found")

    async with httpx.AsyncClient() as client:
        meta = await hibid.fetch_auction_meta(client, [auction.hibid_id])
    auction_meta = meta.get(auction.hibid_id, {})
    if auction_meta.get("premium_mult"):
        auction.buyer_premium_mult = auction_meta["premium_mult"]
        auction.cond_ship = auction_meta.get("cond_ship", False)

    ctx = {"premium_mult": auction.buyer_premium_mult, "source": auction.source}
    job = jobs.start("import", f"Fetching lots from {auction.name}")
    try:
        lots = await hibid.fetch_lots(
            auction.hibid_id, auction_ctx=ctx, category_id=category_id,
            on_progress=lambda fetched, total: jobs.update(
                job, current=fetched, total=total,
                label=f"Fetching lots from {auction.name}"),
            should_cancel=lambda: jobs.is_cancelled(job),
        )
        jobs.update(job, current=0, total=len(lots),
                    label=f"Saving lots from {auction.name}")
    except Exception:
        jobs.finish(job)
        raise

    created = updated = 0
    cancelled = False
    for i, data in enumerate(lots, 1):
        if i % 10 == 0 or i == len(lots):
            if jobs.is_cancelled(job):
                cancelled = True
                break            # keep what's saved so far
            jobs.update(job, current=i)
        row = db.query(models.Lot).filter(models.Lot.lot_id == data["lot_id"]).first()
        if row:
            # bids/status/time-left always come fresh; analysis fields stay
            for k in ("current_bid", "next_bid", "bid_count", "est_cost",
                      "status", "time_left", "thumbnail_url",
                      "hd_thumbnail_url", "fullsize_url"):
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
    jobs.finish(job)
    return {"auction_id": auction_id, "fetched": len(lots),
            "created": created, "updated": updated, "cancelled": cancelled}


@router.post("/{auction_id}/enrich-all", status_code=202)
def enrich_all(auction_id: int, background_tasks: BackgroundTasks,
               skip_hard: bool = False, db: Session = Depends(get_db)):
    """Queue enrichment for every pending/failed lot in an auction. Safe to
    re-run — already-successful lots are skipped. skip_hard leaves out
    HARD-to-ship lots, which rarely clear the ROI bar and cost the same to
    enrich as anything else."""
    q = (db.query(models.Lot).join(models.Enrichment)
           .filter(models.Lot.auction_id == auction_id,
                   models.Enrichment.status.in_(["pending", "failed"])))
    if skip_hard:
        q = q.filter(models.Lot.logistics_ease != "HARD")
    lot_ids = [lot.id for lot in q.all()]
    if not lot_ids:
        return {"auction_id": auction_id, "queued": 0}
    db.query(models.Enrichment).filter(
        models.Enrichment.lot_id.in_(lot_ids)
    ).update({"status": "queued"}, synchronize_session=False)
    db.commit()
    for lid in lot_ids:
        background_tasks.add_task(run_enrichment, lid)
    return {"auction_id": auction_id, "queued": len(lot_ids)}
