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
    # The evidence rows behind est_resale — [{price, title, url, date, kind}]
    comps: Optional[list] = None
    max_bid: Optional[Decimal] = None
    all_in_cost: Optional[Decimal] = None
    est_roi: Optional[float] = None
    profit: Optional[Decimal] = None
    roi_status: Optional[str] = None
    # The gate that blocked a PASS, in plain words; null on a clean gold.
    roi_reason: Optional[str] = None
    gold_check: Optional[str] = None       # confirmed | corrected | demoted | null
    gold_check_note: Optional[str] = None
    identity_note: Optional[str] = None
    fraud_note: Optional[str] = None
    comp_flagged: bool = False
    comp_flag_note: Optional[str] = None
    progress: Optional[str] = None
    error_message: Optional[str] = None
    user_overrides: list[str] = []


class CompFlagRequest(BaseModel):
    """Flag (or clear) a lot's comps as wrong. flagged defaults true so the
    button is a single call; pass flagged=false to clear it."""
    flagged: bool = True
    note: Optional[str] = None


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
    lot_number: Optional[str] = None
    estimate_low: Optional[Decimal] = None
    estimate_high: Optional[Decimal] = None
    auction_id: Optional[int] = None
    title: str
    category: Optional[str] = None
    current_bid: Optional[Decimal] = None
    next_bid: Optional[Decimal] = None
    bid_count: int = 0
    est_cost: Optional[Decimal] = None
    status: Optional[str] = None
    time_left: Optional[str] = None
    closes_at: Optional[datetime] = None
    source: Optional[str] = None
    logistics_ease: Optional[str] = None
    unreachable_pickup: bool = False
    watched: bool = False
    hidden: bool = False
    # Its worth turns on a motor running, which a photo cannot show and the
    # buyer cannot test. Derived from the title (services/powered.py).
    powered_tool: bool = False
    # Ask price divided by the piece count the seller states in the title,
    # for a bulk media lot - "$16" is a steal for 40 CDs and a waste for 2.
    # Null whenever the title does not clearly say how many (services/
    # media_lots.py), which is most lots.
    media_per_item: Optional[float] = None
    # Which never-list rule covers this lot, or null. The label is the
    # reason shown when the never-list toggle hides it, and the rules are
    # the user's own (see routers/never.py) - editable without a deploy.
    never_label: Optional[str] = None
    lot_link: Optional[str] = None
    thumbnail_url: Optional[str] = None
    # How many photos the listing has. One integer, so it costs nothing in a
    # page of a thousand lots, and it is the only way from outside to tell a
    # lot the AI could examine properly from one it saw a single thumbnail
    # of. The URLs themselves stay off the payload - five per lot would put
    # half a megabyte back on a page that was just cut down.
    image_count: Optional[int] = None
    created_at: datetime
    auction_name: Optional[str] = None
    auction_closed: bool = False
    auction_no_us_ship: bool = False   # the house has said it won't ship into the US
    seller_id: Optional[str] = None     # Vinted: whose closet this came from
    seller_name: Optional[str] = None
    # The auction house's estimate-to-hammer calibration (median ratio over
    # its observed closed sales, and how many back it) — shown beside the
    # house estimate so the anchor carries its track record with it.
    house_ratio: Optional[float] = None
    house_ratio_n: int = 0
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
    external_id: Optional[str] = None   # "vt-{query}" marks a Vinted watch
    name: str
    auctioneer: Optional[str] = None
    auctioneer_id: Optional[int] = None
    favorite: bool = False          # a watched auction house
    hidden: bool = False            # dismissed by the user
    lot_count: Optional[int] = None
    lots_missing_open: Optional[int] = None   # open on HiBid, not on file (last full read)
    pickup_info: Optional[str] = None         # pickup days/hours, for auctions you'd drive to
    ship_only: Optional[bool] = None          # pickup text says shipping only
    city: Optional[str] = None
    state: Optional[str] = None
    source: Optional[str] = None
    source_url: Optional[str] = None
    closing_date: Optional[datetime] = None
    buyer_premium_mult: Optional[float] = None
    address: Optional[str] = None
    drive_minutes: Optional[float] = None     # from the saved drive-from address
    live: bool = False                        # Live Auction mode on
    live_refreshed_at: Optional[datetime] = None
    live_auto: bool = False                   # live because lots close within the hour
    imported_at: Optional[datetime] = None
    gold_count: int = 0                       # GOLD MINE lots found so far
    gold_profit: Optional[Decimal] = None     # summed potential profit of those lots
    estimate_ratio: Optional[float] = None    # median estimate-low/hammer for this house
    estimate_ratio_n: int = 0                 # closed sales backing that median
    category_lot_count: Optional[int] = None  # lots matching the last scan's filters
    category_count_for: Optional[int] = None  # which category that count is for
    category_count_search: Optional[str] = None  # which keyword that count is for
    ship_cost_estimate: Optional[float] = None  # AI-read rough $ to ship a small/medium item
    ship_freight_estimate: Optional[float] = None  # AI-read rough $ for an oversized/freight lot
    ship_summary: Optional[str] = None          # one-line shipping-policy summary
    ships_to_us: Optional[bool] = None          # Canadian house: ships into the US? None = unknown
    # What's actually in the database for this auction
    lots_imported: int = 0
    lots_enriched: int = 0
    lots_pending: int = 0
    lots_failed: int = 0
    lots_inspected: int = 0
    lots_hard_pending: int = 0   # HARD-to-ship lots still awaiting enrichment
    lots_unpriced: int = 0       # no value yet - what its comps button would look up
    # Set on a Vinted scan only: how much of the result set was actually read.
    # complete=False means nothing was closed out, because a lot missing from
    # a truncated window has not been shown to be sold.
    scan_complete: Optional[bool] = None
    scan_pages_read: Optional[int] = None
    scan_total_pages: Optional[int] = None
    scan_total_entries: Optional[int] = None


class EnrichBatchRequest(BaseModel):
    lot_ids: list[str]


class LotIdsRequest(BaseModel):
    """A selection of lots, for an action that works on whatever the user
    ticked. The work itself may be per-auction (a bid refresh pulls a whole
    catalogue at a time, because that is the shape of HiBid's API) - the
    endpoint says so in what it returns rather than pretending otherwise."""
    lot_ids: list[str]


class RepriceRequest(BaseModel):
    """An explicit selection for the re-price job. The user ticked these
    lots, so no scope filter applies: a lot with no AI title is searched on
    its raw one, and a lot that already has a value is searched again."""
    lot_ids: list[str]


class UiEventIn(BaseModel):
    name: str
    props: dict | None = None
    view: str | None = None


class UiEventBatch(BaseModel):
    """A few seconds of UI activity, sent together so a click never waits
    on the network."""
    events: list[UiEventIn]


class ImportAllRequest(BaseModel):
    """Bulk import: which auctions, in display order, and how much of each.

    Four shapes, all through this one request:
      - everything                  category_id = -1, bolo_only = False
      - one HiBid category          category_id = <id>
      - only BOLO brand matches     bolo_only = True
      - a keyword search            search_text = "pyrex"

    The filters compose — "pyrex lots in the antiques category" is just
    both category_id and search_text at once — and each is applied
    server-side by HiBid's own lot search, the same query the scan used
    to find the auctions in the first place.
    """
    auction_ids: list[int]
    category_id: int = -1
    bolo_only: bool = False
    search_text: str = ""


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
