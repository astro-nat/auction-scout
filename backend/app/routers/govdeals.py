"""GovDeals endpoints.

POST /govdeals/scan            — search near a zip, upsert one synthetic
                                 auction per seller, return them as cards
POST /govdeals/{id}/import     — pull that seller's assets in as lots

No auction events exist on GovDeals, so the seller IS the auction (one
agency = one pickup trip = the unit the per-auction floor reasons about).
The import runs in the request: everything comes from one already-fetched
search response, no per-lot fetches, so even a big seller lands in a few
seconds — nothing like the HiBid per-catalog crawl that needed the worker.
Bids refresh by re-running import (same upsert); the HiBid bid-refresh
worker skips these rows via their NULL hibid_id.
"""

import logging
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .. import config, models, schemas
from ..database import get_db
from ..services import govdeals
from ..services.hibid import classify_logistics
from .auctions import _attach_stats

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/govdeals", tags=["govdeals"])


class GovDealsScanRequest(BaseModel):
    zip: Optional[str] = None
    radius_miles: Optional[int] = None


def _upsert_auction(db: Session, group: dict) -> models.Auction:
    row = (db.query(models.Auction)
             .filter(models.Auction.external_id == group["external_id"])
             .first())
    name = f"GovDeals: {group['seller']}"
    if row:
        row.name = name
        row.lot_count = len(group["assets"])
        row.closing_date = group["closing_date"]
        row.city, row.state, row.zip = group["city"], group["state"], group["zip"]
    else:
        row = models.Auction(
            external_id=group["external_id"],
            name=name,
            auctioneer=group["seller"],
            lot_count=len(group["assets"]),
            city=group["city"], state=group["state"], zip=group["zip"],
            # GovDeals is overwhelmingly buyer-collects; the radius filter on
            # the scan already guarantees these are within driving range.
            source="Local Pickup",
            source_url=f"https://www.govdeals.com/en/filters/sellers?accountIds={group['account_id']}",
            closing_date=group["closing_date"],
            buyer_premium_mult=config.GOVDEALS_PREMIUM_MULT,
        )
        db.add(row)
    return row


def _save_assets(db: Session, auction: models.Auction,
                 assets: list[dict]) -> tuple[int, int]:
    """Idempotent lot upsert, the save_lots contract: bids and close times
    come fresh on rows we already have, analysis fields stay."""
    created = updated = 0
    for a in assets:
        row = (db.query(models.Lot)
                 .filter(models.Lot.lot_id == a["lot_id"]).first())
        if row:
            row.current_bid = a["current_bid"]
            row.next_bid = a["next_bid"]
            row.bid_count = a["bid_count"]
            row.closes_at = a["closes_at"]
            row.status = "OPEN"
            row.thumbnail_url = a["thumbnail_url"]
            row.fullsize_url = a["fullsize_url"]
            updated += 1
        else:
            row = models.Lot(
                auction_id=auction.id,
                lot_id=a["lot_id"],
                lot_number=f"{a['account_id']}-{a['asset_id']}",
                title=a["title"],
                category=a["category"],
                current_bid=a["current_bid"],
                next_bid=a["next_bid"],
                bid_count=a["bid_count"],
                status="OPEN",
                closes_at=a["closes_at"],
                source="Local Pickup",
                logistics_ease=classify_logistics(a["title"],
                                                  a["category"] or "", ""),
                lot_link=a["lot_link"],
                thumbnail_url=a["thumbnail_url"],
                fullsize_url=a["fullsize_url"],
            )
            db.add(row)
            db.flush()
            db.add(models.Enrichment(lot_id=row.id, status="pending"))
            created += 1
    auction.imported_at = datetime.now()
    return created, updated


@router.post("/scan", response_model=List[schemas.AuctionOut])
def scan(payload: GovDealsScanRequest, db: Session = Depends(get_db)):
    """Search GovDeals near the zip and surface one card per seller.

    Upserts the auction rows only — importing a seller's assets as lots
    (and so making them enrichable) is a separate, deliberate click, same
    as the HiBid flow."""
    zip_code = (payload.zip or config.SOURCING_ZIP).strip()
    miles = payload.radius_miles or config.SOURCING_RADIUS_MILES
    try:
        assets = govdeals.search_assets(zip_code, miles)
    except Exception as exc:  # noqa: BLE001 — surface it, don't 500 opaquely
        logger.warning("GovDeals search failed: %s", exc)
        raise HTTPException(status_code=502,
                            detail=f"GovDeals search failed: {exc}")
    groups = govdeals.group_by_seller(assets)
    auctions = [_upsert_auction(db, g) for g in groups.values()]
    db.commit()
    for a in auctions:
        db.refresh(a)
    return _attach_stats(db, auctions)


@router.post("/{auction_id}/import", status_code=201)
def import_seller(auction_id: int, db: Session = Depends(get_db)):
    """Import (or bid-refresh) every open asset of one GovDeals seller."""
    auction = (db.query(models.Auction)
                 .filter(models.Auction.id == auction_id).first())
    if not auction or not (auction.external_id or "").startswith("gd-"):
        raise HTTPException(status_code=404, detail="Not a GovDeals auction")
    account_id = int(auction.external_id.split("-", 1)[1])
    try:
        assets = govdeals.search_assets(account_ids=[account_id])
    except Exception as exc:  # noqa: BLE001
        logger.warning("GovDeals import failed: %s", exc)
        raise HTTPException(status_code=502,
                            detail=f"GovDeals fetch failed: {exc}")
    # One account can list yards in several states (utility and salvage
    # sellers do). Pickup-only means anything outside the auction's own
    # state is a trip that will never happen — leave those out.
    if auction.state:
        assets = [a for a in assets if a["state"] in (auction.state, None)]
    created, updated = _save_assets(db, auction, assets)
    auction.lot_count = len(assets)
    db.commit()
    return {"auction_id": auction_id, "created": created, "updated": updated}
