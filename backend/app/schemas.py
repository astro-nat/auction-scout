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
    target_buy_price: Optional[Decimal] = None
    confidence: Optional[str] = None
    notes: Optional[str] = None
    error_message: Optional[str] = None


class LotOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    lot_id: str
    title: str
    category: Optional[str] = None
    current_bid: Optional[Decimal] = None
    next_bid: Optional[Decimal] = None
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
