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
    # Identity on other platforms ("gd-{accountId}" for a GovDeals seller's
    # synthetic auction). NULL for HiBid rows; hibid_id NULL for these — the
    # HiBid-only workers (bid refresh, import-all) already filter on that.
    external_id = Column(String, unique=True, index=True)
    name = Column(String, nullable=False)
    auctioneer = Column(String)
    auctioneer_id = Column(Integer, index=True)   # HiBid company id
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
    # Dismissed by the user — a scan keeps re-finding the same
    # auctions, and a house you've judged once shouldn't have to be
    # judged again every time. Kept in the DB, just out of the list.
    hidden = Column(Boolean, default=False)
    # Result of the last filtered scan: how many of this auction's lots
    # matched, and which category/keyword that count refers to. Persisted
    # so the "Import N <category>" / "Import N of 'query'" button survives
    # a page refresh. category_count_search is NULL when the scan that
    # produced the count had no keyword — both fields have to match the
    # scan currently on screen for the stored count to still apply.
    category_lot_count = Column(Integer)
    category_count_for = Column(Integer)
    category_count_search = Column(String)
    # AI-read shipping policy: rough $ to ship a typical small/medium item
    # (fees + handling), a one-line plain-English summary of the policy, and
    # when the analysis ran (so re-runs skip auctions already read).
    ship_cost_estimate = Column(Float)
    ship_summary = Column(String)
    ship_analyzed_at = Column(DateTime)
    # For a house outside the US (province in `state`): will it ship into
    # the US? None = not known; always None for a US house. Read from the
    # terms text at import and by the shipping analysis (services/shipping).
    ships_to_us = Column(Boolean)
    # The closing-window digest (workers/notify.py) fires once per auction as
    # it enters the final WATCH_ALERT_HOURS — this is the once.
    closing_digest_sent_at = Column(DateTime)
    created_at = Column(DateTime, server_default=func.now())

    lots = relationship("Lot", back_populates="auction")


class Lot(Base):
    __tablename__ = "lots"

    id = Column(Integer, primary_key=True)
    lot_id = Column(String, unique=True, nullable=False, index=True)  # hibid lot id
    lot_number = Column(String)       # the house's catalog number ("214A")
    # The house's own estimate range — a ceiling for weak-evidence values,
    # never a price source (see workers/enrich._apply_estimate_cap).
    estimate_low = Column(Numeric)
    estimate_high = Column(Numeric)
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
    # Absolute close time, computed from HiBid's relative "2d 6h 30m" at
    # fetch time — the UI renders a live countdown from this instead of
    # showing a snapshot string that goes stale.
    closes_at = Column(DateTime)
    source = Column(String)           # Ship | Local Pickup (per-lot, beats auction-level)
    logistics_ease = Column(String)   # EASY | NEUTRAL | HARD
    unreachable_pickup = Column(Boolean, default=False)  # nationwide + pickup-only
    # Watch flag: the closing-soon notifier (workers/notify.py) pushes a
    # phone alert when a watched lot's auction closes within the window;
    # the timestamp dedupes so each lot alerts exactly once.
    watched = Column(Boolean, default=False)
    closing_alert_sent_at = Column(DateTime)
    # Manually hidden by the user — stays in the DB (and keeps its
    # enrichment) but drops out of the items view until unhidden.
    hidden = Column(Boolean, default=False)
    lot_link = Column(String)
    thumbnail_url = Column(String)
    hd_thumbnail_url = Column(String)
    fullsize_url = Column(String)
    image_count = Column(Integer, default=0)
    # Every photo the listing has, largest rendition, in the order shown.
    # The first one is not always the item: sellers lead with a stock or
    # catalogue shot, and condition judged from that is condition judged
    # from someone else's product. The deliberate AI look reads several.
    image_urls = Column(JSONB)
    # Who is selling it, where the platform has a seller rather than a
    # house (Vinted). Clicking the name pulls up their whole closet.
    seller_id = Column(String, index=True)
    seller_name = Column(String)
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
    # The evidence behind est_resale: the comp records that survived
    # filtering, as [{price, title, url, date, kind}] — raw observed prices,
    # never scaled. NULL on rows enriched before this column existed; the UI
    # falls back to a sold-listings search link.
    comps = Column(JSONB)

    # --- ROI verdict ---
    max_bid = Column(Numeric)          # highest hammer price that still hits target ROI
    # Everything you actually pay to buy AND resell: hammer + premium +
    # tax + freight in, packing and fee drag. This is the ROI denominator,
    # and it's much larger than est_cost (hammer + premium only).
    all_in_cost = Column(Numeric)
    est_roi = Column(Float)            # at current bid
    profit = Column(Numeric)
    roi_status = Column(String)        # GOLD MINE | PASS
    # WHY the verdict is what it is — the gate that blocked a PASS ("only 1
    # comp — needs 2 agreeing", "bid $21 already past the $4 ceiling") or
    # NULL on a clean GOLD MINE. Refreshed by every _apply_roi run, so it
    # tracks the verdict through bid refreshes and regrades.
    roi_reason = Column(String)
    # Second-opinion audit on GOLD MINEs (workers/enrich._verify_gold):
    # NULL = not yet checked, 'confirmed' = the AI agrees the value is
    # realistic, 'demoted' = it called the value implausible and the
    # verdict fell back to PASS. Cleared whenever est_resale changes.
    gold_check = Column(String)
    gold_check_note = Column(String)
    # Which identifiers the AI title asserted that the listing never had -
    # a brand, a model number, a card number, a quantity - or NULL when none.
    # Set at enrichment and re-price. When set, the comp search used the
    # listing's own title, and the lot cannot be a gold mine: three lots
    # were mispriced by ten times on an invented identity (Topps #221 for
    # an Upper Deck card; DR-M11 for a DRM-555; "Huge Bulk Lot" for ten
    # discs), each with full confidence.
    identity_note = Column(String)
    # Funko fraud and mispricing guards (services/funko.py): what the
    # listing's own words changed about how the lot was priced - an
    # unauthenticated signature, a custom/bootleg, a loose pop.
    fraud_note = Column(String)
    # User-flagged: "these comps came back wrong." Independent of
    # user_overrides — it's a note about the EVIDENCE, not a corrected
    # value, so it survives a reprice instead of blocking one. Feeds future
    # algorithm work: a pile of flagged lots is a worklist of comp-matching
    # failures, grouped by price_source, category, whatever pattern shows up.
    comp_flagged = Column(Boolean, default=False)
    comp_flag_note = Column(String)

    # Which worker a 'queued' lot is waiting for ('enrich' | 'inspect') — how
    # the worker process knows what to run.
    queued_task = Column(String)
    # Queue position. The batch endpoint sends the rows the user can SEE,
    # top first, and that priority has to survive the trip through the
    # database now that a separate process does the picking: order by
    # (queued_at, queue_rank) and both the batch order and plain FIFO
    # across batches fall out.
    queued_at = Column(DateTime)
    queue_rank = Column(Integer)
    # Set when a worker takes the lot. Deliberately NOT a new status value —
    # /status and the UI both count status == 'queued', and a lot being
    # worked is still queued from the user's point of view. A claim old
    # enough to be stale is up for grabs again.
    claimed_at = Column(DateTime)
    # Live play-by-play while the worker runs ("searching eBay comps…");
    # cleared when the lot finishes. The UI polls and shows it on the spinner.
    progress = Column(String)
    error_message = Column(Text)
    last_attempted_at = Column(DateTime)
    # Field names the user has hand-corrected — the worker never overwrites these.
    user_overrides = Column(JSONB, default=list)

    lot = relationship("Lot", back_populates="enrichment")


class Job(Base):
    """A long-running operation the server is working on right now.

    Persisted (rather than in-memory) so a deploy or crash mid-run leaves
    evidence: at startup, workers/resume.py restarts the resumable kinds from
    where `current` points. A finished job deletes its row — a row existing
    means "active, or was active when the process died".
    """
    __tablename__ = "jobs"

    # Short random hex id — part of the /status contract; the frontend's
    # Cancel button posts it back.
    id = Column(String, primary_key=True)
    kind = Column(String, nullable=False)   # scan | import | reprice | ship-analysis
    label = Column(String, nullable=False)  # what the user reads in the status bar
    current = Column(Integer, default=0)    # progress — and the resume checkpoint
    total = Column(Integer)
    detail = Column(String)
    cancelled = Column(Boolean, default=False)
    # Everything a resumable job needs to restart after a deploy/crash
    # (e.g. {"lot_ids": [...]} for reprice). None for request-scoped kinds.
    payload = Column(JSONB)
    # Who is working this row, and when they last said so. A worker that
    # dies mid-job leaves its row behind claiming progress forever; the
    # reaper (workers/maintenance.py) uses a lapsed heartbeat to tell a
    # dead job from a slow one. Before this, clearing one needed a redeploy.
    state = Column(String, default="running")   # pending | running
    claimed_by = Column(String)                 # host:pid of the owner
    heartbeat_at = Column(DateTime)
    started_at = Column(DateTime, server_default=func.now())
    # Where a waiting job stands in line, when the user has reordered the
    # Queue. Lower goes first; unset goes after, oldest first.
    queue_pos = Column(Float)


class FavoriteAuctioneer(Base):
    """An auction house the user wants surfaced first.

    Keyed by HiBid's company id — the number in a company URL, e.g.
    hibid.com/company/149798/budget-barn — so a house that renames itself
    stays favourited.

    This is AuctionScout's own list, not a mirror of HiBid's stars: reading
    those would mean holding the user's HiBid login, which this app doesn't
    do and shouldn't.
    """
    __tablename__ = "favorite_auctioneers"

    auctioneer_id = Column(Integer, primary_key=True)   # HiBid company id
    name = Column(String)
    created_at = Column(DateTime, server_default=func.now())


class EstimateObservation(Base):
    """One closed lot's low estimate against what it actually hammered for —
    the raw material for per-house estimate calibration.

    House estimates are promotional, and how promotional varies by house:
    the Sterling sale's ran 3-5x above realized prices, and anchoring on one
    cost the user $84 on a single lot. Each house's estimate-to-hammer ratio,
    measured from its own closed lots, is what lets the UI say "this house's
    estimates run ~4x hot" next to the anchor.

    Captured by the flush (routers/lots.py) because that is where closed lots
    leave the database — without this table the evidence evaporates within
    hours of every sale. Keyed by HiBid's lot id so re-flushing can't
    double-count. hammer is the last bid the refresher saw before close;
    occasionally stale mid-webcast, which the median shrugs off.
    """
    __tablename__ = "estimate_obs"

    id = Column(Integer, primary_key=True)
    lot_id = Column(String, unique=True, nullable=False)   # HiBid lot id
    auctioneer_id = Column(Integer, index=True, nullable=False)
    estimate_low = Column(Numeric, nullable=False)
    estimate_high = Column(Numeric)
    hammer = Column(Numeric, nullable=False)
    created_at = Column(DateTime, server_default=func.now())


class DismissedAuction(Base):
    """An auction the user has forgotten — never show it again.

    Keyed by HiBid's event id rather than our own row id, because the row is
    not durable: purge_stale_auctions deletes closed auctions that hold no
    lots, which took the Auction.hidden flag with it and let a re-scan
    resurrect a sale the user had already dismissed. This table outlives the
    auction row, so "forget" means forget.

    Scoped to one sale, not the house — the same auctioneer's next sale is a
    fresh judgement (favourite/unfavourite is the house-level control).
    """
    __tablename__ = "dismissed_auctions"

    hibid_id = Column(Integer, primary_key=True)   # HiBid event id
    name = Column(String)                          # for the restore list
    created_at = Column(DateTime, server_default=func.now())


class WorkerHeartbeat(Base):
    """Proof that a worker process exists.

    Once the background work moved out of the API, a dead worker became
    invisible: no request fails, nothing errors, jobs just sit at 'pending'
    looking like they're about to start. /status reads this table so the UI
    can say "queued, but nothing is running" instead of quietly waiting
    forever.
    """
    __tablename__ = "worker_heartbeats"

    id = Column(String, primary_key=True)     # host:pid, from jobs.WORKER_ID
    last_seen = Column(DateTime, nullable=False)
    started_at = Column(DateTime, server_default=func.now())


class TaskTiming(Base):
    """How long one phase of a long job actually took.

    In Postgres rather than the log because Railway keeps a shallow buffer
    and the CLI returns a few dozen lines at a time, which is no use for a
    job that ran for twenty minutes. Rows are small and written once per
    phase, not per item.
    """
    __tablename__ = "task_timings"

    id = Column(Integer, primary_key=True)
    kind = Column(String, nullable=False, index=True)   # import | enrich | ...
    phase = Column(String, nullable=False)              # fetch | save | ...
    job_id = Column(String, index=True)
    auction_id = Column(Integer, index=True)
    label = Column(String)
    started_at = Column(DateTime, nullable=False)
    duration_ms = Column(Float, nullable=False)
    items = Column(Integer)
    per_item_ms = Column(Float)
    # Sub-step totals and anything else worth seeing beside the duration.
    detail = Column(JSONB)
    created_at = Column(DateTime, server_default=func.now())


class PriceObservation(Base):
    """One resale number the app produced for a lot, kept forever.

    est_resale holds the current best figure and gets overwritten; these
    rows are the trail behind it. They exist so the WRONG numbers survive -
    a comp set that overshot tenfold is the only evidence that a filter is
    missing, and overwriting in place threw that away every time.
    """
    __tablename__ = "price_observations"

    id = Column(Integer, primary_key=True)
    lot_id = Column(Integer, ForeignKey("lots.id"), index=True, nullable=False)
    value = Column(Numeric)
    # How it was produced: comps | itemized | retail | audit | ai | cap
    method = Column(String, nullable=False)
    price_source = Column(String)
    comp_count = Column(Integer)
    # Tier from services.price_log.EVIDENCE_RANK - what decides which
    # number the app shows.
    evidence = Column(String, index=True)
    # Did this become the displayed figure, and was it explicitly thrown
    # out (by the audit, or by a cap)?
    chosen = Column(Boolean, default=True)
    rejected = Column(Boolean, default=False)
    note = Column(String)
    # The comp query that produced it, when there was one - the field that
    # says WHY a bad match happened.
    query = Column(String)
    created_at = Column(DateTime, server_default=func.now(), index=True)


class CompCache(Base):
    """Answers from the comp API, kept across restarts.

    The in-process cache is still there as a first hop; this is the one
    that survives a deploy and spans jobs. Empty answers are cached too -
    "nothing sold matching this" is a real answer, and re-asking it once
    per query variant is what burned the quota in the first place.
    """
    __tablename__ = "comp_cache"

    query = Column(String, primary_key=True)
    source = Column(String, primary_key=True)     # soldcomps | active
    # [[price, title], ...] - capped, since a cache is not a store.
    payload = Column(JSONB)
    hits = Column(Integer, default=0)
    created_at = Column(DateTime, server_default=func.now(), index=True)


class ApiReply(Base):
    """The last replies from the comp API, readable from any process.

    A 200 with zero items logs nothing - the warnings fire only on failure -
    so an outage where the API answers politely and emptily was invisible
    from outside: 1,390 lots drifted onto asking prices with no line
    anywhere saying why. An in-memory buffer was tried first and could not
    be read at all: the worker makes the calls and the backend serves
    /status, and they are different containers. Never holds the key.
    """
    __tablename__ = "api_replies"

    id = Column(Integer, primary_key=True)
    source = Column(String, nullable=False, index=True)   # soldcomps
    query = Column(String)
    # HTTP status of the reply. NULL means no request was made at all -
    # no key in this process, or the client raised before sending.
    status = Column(Integer)
    total_items = Column(Integer)     # what the API says matched
    items = Column(Integer)           # how many it returned on this page
    parsed = Column(Integer)          # how many carried a usable price
    note = Column(String)
    created_at = Column(DateTime, server_default=func.now(), index=True)


class UiEvent(Base):
    """One thing the user did in the UI - a button, a filter, a sort, a view.

    Kept in the app's own Postgres and never sent anywhere else. The question
    it exists to answer is "which features get used, and which never do", so
    the UI can be shaped around the real workflow instead of guesses. Names
    are short and stable (button, filter, sort, view, check); the specifics
    ride in props - the button's label with digits normalised, the filter's
    column and value - so counts aggregate instead of fragmenting.
    """
    __tablename__ = "ui_events"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False, index=True)
    view = Column(String)             # auctions | items - where it happened
    props = Column(JSONB)
    created_at = Column(DateTime, server_default=func.now(), index=True)
