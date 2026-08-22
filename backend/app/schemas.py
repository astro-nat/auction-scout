from pydantic import BaseModel, ConfigDict
from typing import Optional
from decimal import Decimal
from datetime import datetime


class EnrichmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    status: str
    bolo_brand: Optional[str] = None
    bolo_category: Optional[str] = None
    bolo_tier: Optional[str] = None
    bolo_confidence: Optional[str] = None
    matched_model: Optional[str] = None
    target_buy_price: Optional[Decimal] = None
    ship_class: Optional[str] = None
    auth_required: bool = False
    enriched_title: Optional[str] = None
    verdict: Optional[str] = None
    confidence: Optional[str] = None
    ai_source: Optional[str] = None
    notes: Optional[str] = None
    est_resale: Optional[Decimal] = None
    price_low: Optional[Decimal] = None
    price_high: Optional[Decimal] = None
    comp_count: int = 0
    price_source: Optional[str] = None
    max_bid: Optional[Decimal] = None
    est_roi: Optional[float] = None
    profit: Optional[Decimal] = None
    roi_status: Optional[str] = None
    progress: Optional[str] = None
    error_message: Optional[str] = None
    user_overrides: list[str] = []


class EnrichmentPatch(BaseModel):
    """User corrections — every field optional; only sent fields are applied.
    logistics_ease lives on the Lot but is corrected through the same endpoint."""
    enriched_title: Optional[str] = None
    verdict: Optional[str] = None
    bolo_brand: Optional[str] = None
    bolo_tier: Optional[str] = None
    est_resale: Optional[Decimal] = None
    notes: Optional[str] = None
    logistics_ease: Optional[str] = None


class LotOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    lot_id: str
    auction_id: Optional[int] = None
    title: str
    category: Optional[str] = None
    current_bid: Optional[Decimal] = None
    next_bid: Optional[Decimal] = None
    bid_count: int = 0
    est_cost: Optional[Decimal] = None
    status: Optional[str] = None
    time_left: Optional[str] = None
    source: Optional[str] = None
    logistics_ease: Optional[str] = None
    unreachable_pickup: bool = False
    lot_link: Optional[str] = None
    thumbnail_url: Optional[str] = None
    created_at: datetime
    enrichment: Optional[EnrichmentOut] = None


class LotCreate(BaseModel):
    lot_id: str
    title: str
    category: Optional[str] = None
    description: Optional[str] = None
    current_bid: Optional[Decimal] = None
    next_bid: Optional[Decimal] = None
    lot_link: Optional[str] = None
    thumbnail_url: Optional[str] = None


class AuctionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    hibid_id: Optional[int] = None
    name: str
    auctioneer: Optional[str] = None
    lot_count: Optional[int] = None
    city: Optional[str] = None
    state: Optional[str] = None
    source: Optional[str] = None
    source_url: Optional[str] = None
    closing_date: Optional[datetime] = None
    buyer_premium_mult: Optional[float] = None
    imported_at: Optional[datetime] = None
    gold_count: int = 0                       # GOLD MINE lots found so far
    gold_profit: Optional[Decimal] = None     # summed potential profit of those lots


class EnrichBatchRequest(BaseModel):
    lot_ids: list[str]


class ScanRequest(BaseModel):
    """Mirrors hibid.com's own search filters."""
    zip: Optional[str] = None
    radius_miles: Optional[int] = None      # -1 = Anywhere (HiBid's option)
    closing_within_days: Optional[int] = None
    include_nationwide: bool = False
    search_text: str = ""
    category_id: int = -1                   # from GET /auctions/categories
    auction_type: str = "ALL"               # ALL | ONLINE | WEBCAST | ABSENTEE | LISTING
    status: str = "OPEN"                    # OPEN | CLOSING | HOT | CLOSED | ALL
