"""PublicSurplus endpoints.

POST /publicsurplus/scan         — search near a zip, upsert ONE synthetic
                                   auction card for the area
POST /publicsurplus/{id}/import  — pull the area's items in as lots

The listing rows don't name the selling agency, so unlike GovDeals there
is no per-seller card: the search area is the auction. Every lot is still
inside the pickup radius — the same guarantee the HiBid radius gives —
and the agency plus exact pickup address are one click away on each
lot's own page. Import doubles as bid refresh, and the HiBid workers
skip these rows via their NULL hibid_id.
"""

import logging
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import or_
from sqlalchemy.orm import Session

from .. import config, models, schemas
from ..database import get_db
from ..services import publicsurplus
from ..services.hibid import classify_logistics
from .auctions import _attach_stats

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/publicsurplus", tags=["publicsurplus"])


class PublicSurplusScanRequest(BaseModel):
    zip: Optional[str] = None
    radius_miles: Optional[int] = None


def _card(db: Session, zip_code: str, miles: int,
          items: list[dict]) -> models.Auction:
    external_id = f"ps-{zip_code}"
    closing = max((i["closes_at"] for i in items if i["closes_at"]),
                  default=None)
    row = (db.query(models.Auction)
             .filter(models.Auction.external_id == external_id).first())
    name = f"PublicSurplus near {zip_code} ({miles} mi)"
    if row:
        row.name = name
        row.lot_count = len(items)
        row.closing_date = closing
    else:
        row = models.Auction(
            external_id=external_id, name=name,
            auctioneer="PublicSurplus",
            lot_count=len(items),
            zip=zip_code, state=config.PUBLICSURPLUS_REGION.upper(),
            source="Local Pickup",
            source_url=("https://www.publicsurplus.com/sms/"
                        f"all,{config.PUBLICSURPLUS_REGION}/browse/search"
                        f"?posting=y&zipCode={zip_code}"
                        f"&milesLocation={miles}"),
            closing_date=closing,
            buyer_premium_mult=config.PUBLICSURPLUS_PREMIUM_MULT,
        )
        db.add(row)
    return row


def _save_items(db: Session, auction: models.Auction,
                items: list[dict]) -> tuple[int, int]:
    """Same idempotent contract as every other import: bids and close
    times refresh on rows we already have, analysis fields stay."""
    created = updated = 0
    for it in items:
        row = (db.query(models.Lot)
                 .filter(models.Lot.lot_id == it["lot_id"]).first())
        if row:
            row.current_bid = it["current_bid"]
            row.next_bid = it["current_bid"]
            row.closes_at = it["closes_at"]
            row.status = "OPEN"
            row.thumbnail_url = it["thumbnail_url"]
            row.fullsize_url = it.get("fullsize_url")
            updated += 1
        else:
            row = models.Lot(
                auction_id=auction.id,
                lot_id=it["lot_id"],
                lot_number=str(it["auction_id"]),
                title=it["title"],
                current_bid=it["current_bid"],
                # The listing grid has no increment; the grader floors the
                # assumed bid at MIN_ASSUMED_BID anyway.
                next_bid=it["current_bid"],
                status="OPEN",
                closes_at=it["closes_at"],
                source="Local Pickup",
                logistics_ease=classify_logistics(it["title"], "", ""),
                lot_link=it["lot_link"],
                thumbnail_url=it["thumbnail_url"],
                fullsize_url=it.get("fullsize_url"),
            )
            db.add(row)
            db.flush()
            db.add(models.Enrichment(lot_id=row.id, status="pending"))
            created += 1
    auction.imported_at = datetime.now()
    return created, updated


@router.post("/scan", response_model=List[schemas.AuctionOut])
def scan(payload: PublicSurplusScanRequest, db: Session = Depends(get_db)):
    """Search PublicSurplus near the zip and surface the area card.
    Importing (making the lots enrichable) stays a separate click."""
    zip_code = (payload.zip or config.SOURCING_ZIP).strip()
    miles = payload.radius_miles or config.SOURCING_RADIUS_MILES
    try:
        items = publicsurplus.search_items(zip_code, miles)
    except Exception as exc:  # noqa: BLE001 — surface it, don't 500 opaquely
        logger.warning("PublicSurplus search failed: %s", exc)
        raise HTTPException(status_code=502,
                            detail=f"PublicSurplus search failed: {exc}")
    if not items:
        return []
    card = _card(db, zip_code, miles, items)
    db.commit()
    db.refresh(card)
    return _attach_stats(db, [card])


@router.post("/backfill-descriptions")
def backfill_descriptions(limit: int = 200, dry_run: bool = True,
                          db: Session = Depends(get_db)):
    """Fetch the seller's item-page text - and its photo list - for
    PublicSurplus lots that have none. Free - no AI, one page per lot - and it is what tells the pricer
    a laptop has no hard drive. Lots imported before this existed all have
    an empty description; enrichment fetches it for new ones by itself.

    dry_run counts what would be fetched. `limit` caps one call so a few
    hundred pages are not pulled in a single request.
    """
    q = (db.query(models.Lot)
           .filter(models.Lot.lot_id.like("ps-%"),
                   or_(models.Lot.description.is_(None),
                       models.Lot.description == ""))
           .order_by(models.Lot.id.desc()))
    total = q.count()
    if dry_run:
        return {"missing": total, "dry_run": True}
    filled = failed = 0
    for lot in q.limit(limit).all():
        detail = publicsurplus.fetch_detail(int(lot.lot_id[3:])) or {}
        text = detail.get("description")
        if detail.get("images"):
            lot.image_urls = detail["images"]
            lot.image_count = len(detail["images"])
        if text:
            lot.description = text
            filled += 1
        else:
            failed += 1
    db.commit()
    return {"missing_before": total, "filled": filled,
            "no_text": failed, "remaining": max(0, total - filled - failed)}


@router.post("/{auction_id}/import", status_code=201)
def import_area(auction_id: int, db: Session = Depends(get_db)):
    """Import (or bid-refresh) every open item in the area card."""
    auction = (db.query(models.Auction)
                 .filter(models.Auction.id == auction_id).first())
    if not auction or not (auction.external_id or "").startswith("ps-"):
        raise HTTPException(status_code=404,
                            detail="Not a PublicSurplus auction")
    zip_code = auction.external_id.split("-", 1)[1]
    try:
        items = publicsurplus.search_items(zip_code,
                                           config.SOURCING_RADIUS_MILES)
    except Exception as exc:  # noqa: BLE001
        logger.warning("PublicSurplus import failed: %s", exc)
        raise HTTPException(status_code=502,
                            detail=f"PublicSurplus fetch failed: {exc}")
    created, updated = _save_items(db, auction, items)
    auction.lot_count = len(items)
    db.commit()
    return {"auction_id": auction_id, "created": created, "updated": updated}
