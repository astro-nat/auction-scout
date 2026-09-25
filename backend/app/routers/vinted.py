"""Vinted endpoints.

POST /vinted/seller/{id}/import — one seller's whole closet. The member
page renders its items in the browser, so there is nothing to scrape; it
calls /api/v2/wardrobe/<id>/items and so do we, which is the only way to
see everything someone has listed rather than the slice a keyword found.

POST /vinted/scan — one call does scan AND import: the query is itself
the deliberate choice (unlike a platform-wide radius sweep there's no
"which seller" decision between seeing and importing), and re-running it
IS the watch: fresh listings appear as new lots, changed asks refresh,
and anything no longer in the results — sold or delisted — is marked
CLOSED so the default filters drop it.

Fixed prices, not bids: current_bid and next_bid are the asking price,
so the grader's "bid already past the ceiling" reads as "asking price
past what it's worth to you", and max_bid is the most an OFFER should
ever be. Economics ride the auction row: ~5% + $0.70 buyer protection
approximated by the premium multiplier, inbound shipping by the ship
cost estimate.
"""

import logging
import re
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .. import config, models, schemas
from ..database import get_db
from ..services import vinted
from ..services.hibid import classify_logistics
from .auctions import _attach_stats

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/vinted", tags=["vinted"])


class VintedScanRequest(BaseModel):
    query: str
    max_price: Optional[float] = None


def _slug(query: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", query.lower()).strip("-")[:40]


def _seller_id(item: dict) -> str | None:
    sid = item.get("seller_id")
    return str(sid) if sid else None


def _save_items(db: Session, auction: models.Auction, items: list[dict],
                seller_name: str | None = None) -> tuple[int, int]:
    """Upsert listings onto an auction row. Same contract as every other
    import: prices and status refresh, analysis fields are left alone."""
    created = updated = 0
    for it in items:
        row = (db.query(models.Lot)
                 .filter(models.Lot.lot_id == it["lot_id"]).first())
        if row:
            row.current_bid = it["price"]
            row.next_bid = it["price"]
            row.status = "OPEN"
            row.thumbnail_url = it["thumbnail_url"] or row.thumbnail_url
            row.seller_id = row.seller_id or _seller_id(it)
            row.seller_name = seller_name or row.seller_name
            updated += 1
        else:
            detail = ", ".join(x for x in (
                f"Brand: {it['brand']}" if it.get("brand") else None,
                f"Seller-stated condition: {it['condition']}"
                if it.get("condition") else None) if x)
            row = models.Lot(
                auction_id=auction.id,
                lot_id=it["lot_id"],
                lot_number=str(it["item_id"]),
                title=it["title"],
                category=it.get("brand"),
                description=detail or None,
                current_bid=it["price"],
                next_bid=it["price"],
                status="OPEN",
                source="Ship",
                logistics_ease=classify_logistics(it["title"],
                                                  it.get("brand") or "", ""),
                lot_link=it["lot_link"],
                thumbnail_url=it["thumbnail_url"],
                seller_id=_seller_id(it),
                seller_name=seller_name,
            )
            db.add(row)
            db.flush()
            db.add(models.Enrichment(lot_id=row.id, status="pending"))
            created += 1
    return created, updated


@router.post("/seller/{user_id}/import", response_model=List[schemas.AuctionOut])
def import_seller(user_id: int, db: Session = Depends(get_db)):
    """Import everything one Vinted seller currently has for sale.

    Their closet becomes an auction of its own, so it sorts, filters and
    prices like any other - and re-running it is the watch: new listings
    appear, asks refresh, and anything gone from the closet is marked
    CLOSED."""
    try:
        found = vinted.fetch_wardrobe(user_id)
    except Exception as exc:  # noqa: BLE001 - surface it, don't 500 opaquely
        logger.warning("Vinted closet fetch failed for %s: %s", user_id, exc)
        raise HTTPException(status_code=502,
                            detail=f"Vinted closet fetch failed: {exc}")
    items = found["items"]
    login = found["seller"].get("login") or str(user_id)

    external_id = f"vt-user-{user_id}"
    auction = (db.query(models.Auction)
                 .filter(models.Auction.external_id == external_id).first())
    if not auction:
        auction = models.Auction(
            external_id=external_id,
            name=f"Vinted closet: {login}",     # NOT NULL, and the flush is next
            auctioneer="Vinted",
            source="Ship",
            buyer_premium_mult=config.VINTED_PREMIUM_MULT,
            ship_cost_estimate=config.VINTED_SHIP_ESTIMATE,
        )
        db.add(auction)
        db.flush()
    auction.name = f"Vinted closet: {login}"
    auction.source_url = vinted.member_link(user_id)
    auction.lot_count = len(items)
    auction.imported_at = datetime.now()
    auction.closing_date = datetime(2099, 1, 1)

    created, updated = _save_items(db, auction, items, seller_name=login)

    fresh = {it["lot_id"] for it in items}
    closed = 0
    for row in (db.query(models.Lot)
                  .filter(models.Lot.auction_id == auction.id,
                          models.Lot.status == "OPEN").all()):
        if row.lot_id not in fresh:
            row.status = "CLOSED"
            closed += 1
    db.commit()
    db.refresh(auction)
    logger.info("Vinted closet %s (%s): %d new, %d refreshed, %d closed",
                user_id, login, created, updated, closed)
    return _attach_stats(db, [auction])


@router.post("/scan", response_model=List[schemas.AuctionOut])
def scan(payload: VintedScanRequest, db: Session = Depends(get_db)):
    query = payload.query.strip()
    if not query:
        raise HTTPException(status_code=422, detail="query is required")
    try:
        items = vinted.search_items(query, payload.max_price)
    except Exception as exc:  # noqa: BLE001 — surface it, don't 500 opaquely
        logger.warning("Vinted search failed: %s", exc)
        raise HTTPException(status_code=502,
                            detail=f"Vinted search failed: {exc}")

    external_id = f"vt-{_slug(query)}"
    auction = (db.query(models.Auction)
                 .filter(models.Auction.external_id == external_id).first())
    if not auction:
        auction = models.Auction(
            external_id=external_id,
            name=f"Vinted: {query}",
            auctioneer="Vinted",
            source="Ship",
            source_url=(f"https://www.vinted.com/catalog"
                        f"?search_text={query}&order=newest_first"),
            # ~5% buyer protection + $0.70 + tax drag, erring high.
            buyer_premium_mult=config.VINTED_PREMIUM_MULT,
            ship_cost_estimate=config.VINTED_SHIP_ESTIMATE,
        )
        db.add(auction)
        db.flush()
    auction.name = f"Vinted: {query}"
    auction.lot_count = len(items)
    auction.imported_at = datetime.now()
    # Fixed-price listings don't close on a date; a far-future stamp keeps
    # every "still open" filter honest without a special case per query.
    auction.closing_date = datetime(2099, 1, 1)

    created = updated = 0
    fresh_ids = {it["lot_id"] for it in items}
    for it in items:
        row = (db.query(models.Lot)
                 .filter(models.Lot.lot_id == it["lot_id"]).first())
        if row:
            row.current_bid = it["price"]
            row.next_bid = it["price"]
            row.status = "OPEN"
            row.thumbnail_url = it["thumbnail_url"]
            row.seller_id = row.seller_id or _seller_id(it)
            updated += 1
        else:
            detail = ", ".join(x for x in (
                f"Brand: {it['brand']}" if it["brand"] else None,
                f"Seller-stated condition: {it['condition']}"
                if it["condition"] else None) if x)
            row = models.Lot(
                auction_id=auction.id,
                lot_id=it["lot_id"],
                lot_number=str(it["item_id"]),
                title=it["title"],
                category=it["brand"],
                description=detail or None,
                current_bid=it["price"],
                next_bid=it["price"],
                status="OPEN",
                source="Ship",
                logistics_ease=classify_logistics(it["title"],
                                                  it["brand"] or "", ""),
                lot_link=it["lot_link"],
                thumbnail_url=it["thumbnail_url"],
                seller_id=_seller_id(it),
            )
            db.add(row)
            db.flush()
            db.add(models.Enrichment(lot_id=row.id, status="pending"))
            created += 1

    # Gone from the results = sold or delisted; either way not buyable.
    closed = 0
    for row in (db.query(models.Lot)
                  .filter(models.Lot.auction_id == auction.id,
                          models.Lot.status == "OPEN").all()):
        if row.lot_id not in fresh_ids:
            row.status = "CLOSED"
            closed += 1

    db.commit()
    db.refresh(auction)
    logger.info("Vinted scan %r: %d new, %d refreshed, %d closed",
                query, created, updated, closed)
    return _attach_stats(db, [auction])
