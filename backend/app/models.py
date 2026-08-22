from sqlalchemy import (
    Column, Integer, String, Text, Numeric, DateTime, Boolean, Float,
    ForeignKey, func
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship
from .database import Base


class Auction(Base):
    __tablename__ = "auctions"

    id = Column(Integer, primary_key=True)
    hibid_id = Column(Integer, unique=True, index=True)  # HiBid event id
    name = Column(String, nullable=False)
    auctioneer = Column(String)
    lot_count = Column(Integer)
    city = Column(String)
    state = Column(String)
    zip = Column(String)
    source = Column(String)          # Local Pickup | Ship
    source_url = Column(String)
    closing_date = Column(DateTime)
    buyer_premium_mult = Column(Float)   # 1.15 = 15% premium; None = unknown
    cond_ship = Column(Boolean, default=False)  # "shipping on some lots only"
    imported_at = Column(DateTime)   # when lots were last pulled
    # Result of the last category-filtered scan: how many of this auction's
    # lots matched, and which HiBid category that count refers to. Persisted
    # so the "Import N <category>" button survives a page refresh.
    category_lot_count = Column(Integer)
    category_count_for = Column(Integer)
    # AI-read shipping policy: rough $ to ship a typical small/medium item
    # (fees + handling), a one-line plain-English summary of the policy, and
    # when the analysis ran (so re-runs skip auctions already read).
    ship_cost_estimate = Column(Float)
    ship_summary = Column(String)
    ship_analyzed_at = Column(DateTime)
    created_at = Column(DateTime, server_default=func.now())

    lots = relationship("Lot", back_populates="auction")


class Lot(Base):
    __tablename__ = "lots"

    id = Column(Integer, primary_key=True)
    lot_id = Column(String, unique=True, nullable=False, index=True)  # hibid lot id
    auction_id = Column(Integer, ForeignKey("auctions.id"))
    title = Column(String, nullable=False)
    category = Column(String, index=True)
    description = Column(Text)
    current_bid = Column(Numeric)
    next_bid = Column(Numeric)
    bid_count = Column(Integer, default=0)
    est_cost = Column(Numeric)        # effective bid × buyer-premium multiplier
    status = Column(String)           # HiBid lot status
    time_left = Column(String)
    source = Column(String)           # Ship | Local Pickup (per-lot, beats auction-level)
    logistics_ease = Column(String)   # EASY | NEUTRAL | HARD
    unreachable_pickup = Column(Boolean, default=False)  # nationwide + pickup-only
    lot_link = Column(String)
    thumbnail_url = Column(String)
    hd_thumbnail_url = Column(String)
    fullsize_url = Column(String)
    image_count = Column(Integer, default=0)
    created_at = Column(DateTime, server_default=func.now())

    auction = relationship("Auction", back_populates="lots")
    enrichment = relationship("Enrichment", back_populates="lot", uselist=False)


class Enrichment(Base):
    __tablename__ = "enrichment"

    id = Column(Integer, primary_key=True)
    lot_id = Column(Integer, ForeignKey("lots.id"), unique=True, nullable=False)

    status = Column(String, default="pending", index=True)  # pending | queued | success | failed

    # --- BOLO match (deterministic, from the curated brand files) ---
    bolo_brand = Column(String)
    bolo_category = Column(String)
    bolo_tier = Column(String)
    bolo_confidence = Column(String)   # strong | alias_only | model_only
    matched_model = Column(String)
    target_buy_price = Column(Numeric)  # top of the BOLO target-buy range
    ship_class = Column(String)
    # Luxury-tier match (watches, designer, sneakers): resale value hinges on
    # authentication, so comps/ROI can't be trusted until verified in person.
    auth_required = Column(Boolean, default=False)

    # --- AI enrichment (Claude text/vision, via the worker only) ---
    enriched_title = Column(String)    # eBay-searchable title
    verdict = Column(String)           # condition verdict
    confidence = Column(String)        # strong | weak
    ai_source = Column(String)         # text | vision | none
    notes = Column(Text)

    # --- marketplace comps ---
    est_resale = Column(Numeric)
    price_low = Column(Numeric)
    price_high = Column(Numeric)
    comp_count = Column(Integer, default=0)
    price_source = Column(String)

    # --- ROI verdict ---
    max_bid = Column(Numeric)          # highest hammer price that still hits target ROI
    est_roi = Column(Float)            # at current bid
    profit = Column(Numeric)
    roi_status = Column(String)        # GOLD MINE | PASS

    # Live play-by-play while the worker runs ("searching eBay comps…");
    # cleared when the lot finishes. The UI polls and shows it on the spinner.
    progress = Column(String)
    error_message = Column(Text)
    last_attempted_at = Column(DateTime)
    # Field names the user has hand-corrected — the worker never overwrites these.
    user_overrides = Column(JSONB, default=list)

    lot = relationship("Lot", back_populates="enrichment")
