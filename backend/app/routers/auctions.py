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
from ..services import hibid
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
        q = q.filter(or_(models.Auction.closing_date.is_(None),
                         models.Auction.closing_date >= datetime.now()))
    auctions = q.order_by(models.Auction.closing_date).all()
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
    for a in auctions:
        a.gold_count, a.gold_profit = gold.get(a.id, (0, 0))
    return auctions


@router.get("/categories")
async def list_categories():
    """HiBid's top-level category tree, for the scan filter dropdown."""
    return await hibid.fetch_categories()


@router.post("/scan", response_model=List[schemas.AuctionOut])
async def scan_auctions(payload: schemas.ScanRequest, db: Session = Depends(get_db)):
    """Discover open auctions near the configured zip and store them."""
    found = await hibid.discover_auctions(
        zip_code=payload.zip,
        radius_miles=payload.radius_miles,
        closing_within_days=payload.closing_within_days,
        include_nationwide=payload.include_nationwide,
        search_text=payload.search_text,
        category_id=payload.category_id,
        auction_type=payload.auction_type,
        status=payload.status,
    )
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
    return stored


@router.post("/{auction_id}/import")
async def import_lots(auction_id: int, db: Session = Depends(get_db)):
    """Pull every open lot for one auction into Postgres (idempotent upsert)."""
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
    lots = await hibid.fetch_lots(auction.hibid_id, auction_ctx=ctx)

    created = updated = 0
    for data in lots:
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
    return {"auction_id": auction_id, "fetched": len(lots),
            "created": created, "updated": updated}


@router.post("/{auction_id}/enrich-all", status_code=202)
def enrich_all(auction_id: int, background_tasks: BackgroundTasks,
               db: Session = Depends(get_db)):
    """Queue enrichment for every pending/failed lot in an auction. Safe to
    re-run — already-successful lots are skipped."""
    lot_ids = [
        lot.id for lot in
        db.query(models.Lot).join(models.Enrichment)
          .filter(models.Lot.auction_id == auction_id,
                  models.Enrichment.status.in_(["pending", "failed"]))
          .all()
    ]
    if not lot_ids:
        return {"auction_id": auction_id, "queued": 0}
    db.query(models.Enrichment).filter(
        models.Enrichment.lot_id.in_(lot_ids)
    ).update({"status": "queued"}, synchronize_session=False)
    db.commit()
    for lid in lot_ids:
        background_tasks.add_task(run_enrichment, lid)
    return {"auction_id": auction_id, "queued": len(lot_ids)}
