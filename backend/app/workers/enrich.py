"""
Enrichment worker — the full per-lot pipeline.

This is the ONLY file in the backend that imports the Anthropic SDK. Keeping the
AI call isolated here — rather than in a route handler — is what makes it safe for
this call to be slow or occasionally fail without taking the API down with it.

Pipeline per lot (cheapest signal first):
  1. BOLO match      — deterministic regex over the curated brand files. Free, instant.
  2. AI title/verdict — Claude text pass when the description carries signal;
                        Claude vision on the thumbnail otherwise. Produces an
                        eBay-searchable title + a condition verdict.
  3. Comps            — SoldComps / eBay Browse via services.pricing. Free-ish.
  4. ROI              — pure math via services.financials.

These workers run via FastAPI BackgroundTasks (see routers/enrichment.py), which
die with the process — so everything long-running leaves its plan in the DB and
survives a deploy: batch runs (run_reprice, run_ship_analysis) persist a jobs-table
row holding their id list and a `current` checkpoint, and per-lot work rides the
'queued' status on the enrichment table. workers/resume.py picks all of it back up
at startup.
"""

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import os
import re
import base64
import logging
from datetime import datetime, timezone

import anthropic
import httpx
from sqlalchemy import or_
from sqlalchemy.orm import Session, joinedload

from ..database import SessionLocal
from .. import models, config
from ..services import financials, gemini, hibid, jobs, price_log, pricing
from ..services import settings as settings_store
from ..services.bolo import BoloMatcher
from ..services.hibid import classify_logistics
from ..services.timing import timed

logger = logging.getLogger(__name__)

client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env
MODEL = "claude-haiku-4-5"      # cheap + fast; per-lot cost matters at 800 lots/auction

bolo_matcher = BoloMatcher()    # hot-reloads its JSON files on mtime change

MAX_ATTEMPTS = 2
MIN_DESC_FOR_TEXT_PASS = 80     # below this the description carries no signal

# What it actually costs YOU to move one item, by logistics tier.
#
# Outbound postage is NOT in here: listings use eBay calculated shipping, so
# the buyer pays the carrier. What the seller still eats is (a) packing
# materials and (b) eBay's final-value fee, which is charged on the shipping
# the buyer paid as well as on the item. So each tier is roughly:
#     EASY    ~$2 mailer/small box  + 15% of ~$9  postage  = $3.50
#     NEUTRAL ~$4 box and fill      + 15% of ~$16 postage  = $6.50
#     HARD    ~$12 heavy/fragile    + 15% of ~$60 postage  = $21.00
#
# The old table was $15/$25/$60 and sat on top of a separate $15 "packing
# buffer" — $30 of flat overhead on an EASY item, which was 91% of the
# median lot's all-in cost and swamped everything the ROI was meant to show.
LOGISTICS_COST = {"EASY": 3.50, "NEUTRAL": 6.50, "HARD": 21.00}

# The audit sweep promotes thin-evidence lots (no comps, value from the
# itemized vision pass) when at least this much profit is on the table —
# a half-cent audit isn't worth spending on a $4 pile.
PROMOTE_MIN_PROFIT = float(os.environ.get("PROMOTE_MIN_PROFIT", "15"))

# Getting the lot FROM the auction house is a separate, real cost that the
# model ignored entirely — ship_cost_estimate was read off each auction's
# terms page and then never used by anything. It's zero when you drive out
# and collect the lot yourself.
#
# The auction quotes a typical small-item rate, so scale it by how bulky
# this particular lot is.
INBOUND_TIER_MULT = {"EASY": 1.0, "NEUTRAL": 1.5, "HARD": 4.0}
# Used when an auction ships but its terms haven't been read yet.
DEFAULT_INBOUND_SHIP = 15.0
# A HARD lot doesn't go in a box. Scaling the house's small-parcel quote
# can't reach what a pallet costs — 4x a $12 quote is $48, against $150-400
# of real LTL freight — and the gap manufactured gold mines: the Pearland
# industrial audit had 52 of its 62 golds on HARD lots costed at $21,
# including a ceiling-mounted air purification station.
#
# Only bites when the lot actually ships; local pickup still costs nothing.
FREIGHT_FLOOR = {"HARD": float(os.environ.get("HARD_FREIGHT_FLOOR", "150"))}


def _inbound_shipping(lot: models.Lot) -> float:
    """What the auction house charges to get this lot to you.

    Per-lot rather than per-shipment: winning five lots from one auction
    usually combines into one box, so this is conservative by design — it
    prices each lot as if you bought only that one.
    """
    auction = lot.auction
    source = (lot.source or (auction.source if auction else None) or "").lower()
    if "pickup" in source:
        return 0.0          # local pickup — you collect it, nothing to pay
    tier = lot.logistics_ease or "NEUTRAL"
    quoted = getattr(auction, "ship_cost_estimate", None) if auction else None
    base = float(quoted) if quoted else DEFAULT_INBOUND_SHIP
    scaled = base * INBOUND_TIER_MULT.get(tier, 1.5)
    return round(max(scaled, FREIGHT_FLOOR.get(tier, 0.0)), 2)

# When itemized inspection finds no comps for an item, the model's own
# sold-price estimate (from the same vision call) fills the gap — discounted,
# because model guesses skew optimistic vs. real sold data. env-tunable like
# ACTIVE_REALIZATION in services/pricing.py.
AI_ESTIMATE_REALIZATION = float(os.environ.get("AI_ESTIMATE_REALIZATION", "0.8"))

# Keeper items past the biggest one sell at a discount when a lot is parted
# out: every extra item is another photo, another listing, another parcel.
# Applied to the sum of the smaller keepers; the headline item keeps full
# value. ("Misc Christmas decor" summed 12 items at full price to $188.)
PARTOUT_REALIZATION = float(os.environ.get("PARTOUT_REALIZATION", "0.6"))

# Grade lots as if they'll hammer for at least this much. Auctions open at
# $0-1, and a real resale divided by a $1 bid manufactures 900% ROIs that
# sort to the top while meaning nothing — nothing worth grading hammers
# under a few dollars.
MIN_ASSUMED_BID = float(os.environ.get("MIN_ASSUMED_BID", "5"))

# A weak-evidence resale value may not exceed this fraction of the house's
# own LOW estimate. Estimates are marketing, but a house rarely lowballs its
# own consignment — the low end works as a ceiling. Strong sold comps and
# retail-in-title stand as-is: real data beats the house's guess.
ESTIMATE_REALIZATION = float(os.environ.get("ESTIMATE_REALIZATION", "0.8"))


def _apply_estimate_cap(lot: models.Lot, e: models.Enrichment) -> None:
    """Clamp resale values against the house's own estimate range.

    Weak evidence (asking prices, AI estimates, <3 sold comps) may not
    exceed ESTIMATE_REALIZATION × the LOW end. Strong sold comps may roam
    the whole range but not past the HIGH end — a specialist house knows
    its consignment, and comps blowing past its own ceiling means the
    search matched something better than what's in the case (a $100-200
    pendant "worth" $300+ came from exactly that). Retail-in-title and
    hand-set prices are untouchable. Runs before _apply_roi wherever
    est_resale is set."""
    low = getattr(lot, "estimate_low", None)
    if not low or not e.est_resale:
        return
    src = e.price_source or ""
    if src.startswith("retail $") or "est_resale" in set(e.user_overrides or []):
        return
    strong = ("sold" in src.lower() and "active" not in src.lower()
              and (e.comp_count or 0) >= 3)
    high = getattr(lot, "estimate_high", None) or low
    cap = (float(high) if strong
           else round(float(low) * ESTIMATE_REALIZATION, 2))
    if float(e.est_resale) <= cap:
        return
    price_log.record(lot.id, float(e.est_resale), method="comps",
                     price_source=src, comp_count=e.comp_count,
                     chosen=False, rejected=True,
                     note=f"above house cap ${cap:g}, capped")
    e.est_resale = cap
    e.price_source = (src + (f" → capped at house-high ${float(high):g}" if strong
                             else f" → capped at {ESTIMATE_REALIZATION:g}× "
                                  f"house-low ${float(low):g}"))


# Every fresh GOLD MINE gets a second-opinion AI audit before it's allowed
# to stand — a GOLD MINE is the app telling you to spend money, and the
# audits kept finding golds built on the wrong comps entirely (costume
# jewelry priced as fine, one item carrying a pile's total). Roughly half a
# cent per gold with the photo. "0"/"false" disables.
GOLD_CHECK = os.environ.get("GOLD_CHECK", "1").lower() not in ("0", "false", "off")


# Titles that describe a PILE, not a product. These route to the itemized
# vision pass instead of the single-item enrich: comping "Lot of Assorted
# Kitchen Items" as one product is meaningless, while inspect identifies
# and prices each thing it can see. Deliberately narrow — "set" and "pair"
# are excluded because a flatware set or a pair of lamps is usually one
# sellable unit, not a pile.
_MULTI_ITEM_RE = re.compile(
    r"\b(lots?|bundles?|assorted|miscellaneous|misc\.?"
    r"|(?:box|bag|tote|crate|tray|group|grouping|collection|mix)\s+of)\b",
    re.IGNORECASE)


def looks_multi_item(title: str) -> bool:
    return bool(_MULTI_ITEM_RE.search(title or ""))


def _sane_estimate(v) -> float | None:
    """The model's est_value, if it's a usable number. Rejects junk (strings,
    negatives, zero) and absurd guesses that would poison the lot total."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    if not (0 < float(v) <= 5000):
        return None
    return float(v)


# Comps mostly describe working/complete examples — often brand new. What the
# AI saw in the photo has to move the number, or a broken unit inherits a
# working unit's price.
CONDITION_MULTIPLIER = {
    "broken, damaged, or for parts": 0.25,
    "untested or unknown condition": 0.70,
    "normal wear and tear": 1.00,
    "mint condition or working perfectly": 1.00,
}

TEXT_PROMPT = """You are enriching a resale-auction lot for a reseller.
From the title and description, produce:
- enriched_title: the most specific eBay-searchable title (brand, model, era, product type; under 80 chars; no fluff or condition words). For apparel/shoes, ALWAYS carry the audience and size when stated (kids/youth/toddler/boys/girls/men's/women's, size) — a kids Nike hoodie priced against adult listings is a wrong price.
- verdict: exactly one of "broken, damaged, or for parts" | "untested or unknown condition" | "mint condition or working perfectly" | "normal wear and tear"
- confident: true only if brand AND specific product type are identifiable
- notes: one sentence of resale-relevant context
- ship: how hard this item is to ship, exactly one of "EASY" (fits a padded mailer or small box: clothing, media, jewelry, small electronics) | "NEUTRAL" (normal parcel) | "HARD" (furniture, appliances, oversized/freight, or pickup-only)

Title: {title}
Description: {description}

Return ONLY valid JSON: {{"enriched_title": string, "verdict": string, "confident": boolean, "notes": string, "ship": string}}
"""

SHIPPING_PROMPT = """You are helping an auction reseller decide whether it's worth having items shipped from this auction house.
Read the auction's shipping info and terms below and work out what shipping actually costs the buyer.

Shipping info:
{ship_text}

Terms and conditions (may repeat or contradict the shipping info — the more specific fee schedule wins):
{terms_text}

Produce:
- ships: true if the auctioneer or a third party will ship, false if pickup-only, null if the text doesn't say
- cost_estimate: your rough TOTAL cost in USD to ship one typical small-to-medium item (a shoebox-sized package): carrier postage + any handling/packing/per-item/flat fees mentioned. Use mid-range carrier rates (~$10-15 postage for such a package) when the text only gives fees on top. null ONLY when ships is false or null — if shipping exists but the fees are vague or "determined after packing", still commit to your best mid-range guess rather than null.
- summary: one plain-English sentence a reseller can act on, e.g. "Ships in-house: $5/item handling + carrier rate, so roughly $18 for a small box" or "Third-party UPS Store — expect $25+ minimum" or "Pickup only, no shipping"

Return ONLY valid JSON: {{"ships": boolean or null, "cost_estimate": number or null, "summary": string}}
"""

VISION_PROMPT = """Identify this auction lot from its photo for an eBay search.
Produce the most specific searchable title you can (brand, model, material, era, product type; under 80 chars).
For apparel/shoes, ALWAYS carry the audience and size when the photo, tags, listing title, or description show it (kids/youth/toddler/boys/girls/men's/women's, size) — a kids item priced against adult listings is a wrong price.
A vintage/unbranded item is still confident if you can name 3+ visual specifics (material + color/pattern + form + era).
Mixed/bundled lots are never confident.

Original listing title: {title}
Listing description (may be empty or boilerplate — trust the photo over it, but use its sizes/model numbers): {description}

Return ONLY valid JSON: {{"enriched_title": string, "verdict": string, "confident": boolean, "notes": string, "ship": string}}
verdict must be exactly one of "broken, damaged, or for parts" | "untested or unknown condition" | "mint condition or working perfectly" | "normal wear and tear"
ship judges how hard the pictured item is to ship: exactly one of "EASY" (fits a padded mailer or small box) | "NEUTRAL" (normal parcel) | "HARD" (furniture, appliance, oversized/freight)
"""


def _parse_json_response(text: str) -> dict:
    """Models wrap JSON in ```fences or append commentary despite being told not
    to. Parse the first JSON object in the text and ignore everything around it."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        text = text.removesuffix("```").strip()
    start = text.find("{")
    if start == -1:
        raise ValueError(f"no JSON object in response: {text[:120]!r}")
    obj, _ = json.JSONDecoder().raw_decode(text[start:])
    return obj


def _mark_ai_ship(e: models.Enrichment) -> None:
    """Record that the AI (not the import-time title regex) set this lot's
    ship tier, by riding the user_overrides JSON list with a sentinel. The
    reprice pass reclassifies regex-set tiers with current rules but must
    never clobber an AI judgment; the sentinel is how it tells them apart.
    ("logistics_ease" itself in the list means the USER set it — that blocks
    both the AI and the reprice.)"""
    marks = set(e.user_overrides or [])
    if "logistics_ease_ai" not in marks:
        e.user_overrides = sorted(marks | {"logistics_ease_ai"})


def _progress(db: Session, e: models.Enrichment, text: str | None) -> None:
    """Publish a live stage update — the UI polls and shows this next to the
    spinner, so the user sees exactly what the worker is doing right now."""
    e.progress = text
    db.commit()


def run_enrichment(lot_db_id: int) -> None:
    """Entry point called from the background task. Opens its own DB session
    since it runs outside the request/response cycle's session lifetime."""
    db: Session = SessionLocal()
    try:
        lot = db.query(models.Lot).filter(models.Lot.id == lot_db_id).first()
        if not lot:
            logger.warning("Lot %s vanished before enrichment ran", lot_db_id)
            return

        e = lot.enrichment
        # Cancelling a batch flips queued lots back to 'pending'; anything
        # not still queued was cancelled before its turn came up.
        if e.status != "queued":
            return

        # Multi-item lots go to the itemized vision pass instead: pricing a
        # "Lot of Assorted Tools" as if it were one product is meaningless.
        # Needs a photo to inspect; without one, fall through to enrich.
        # A printed retail price covers the whole lot, so it makes the vision
        # pass an expense with nothing to buy.
        if (looks_multi_item(lot.title)
                and pricing.retail_from_title(lot.title) is None
                and (lot.fullsize_url or lot.hd_thumbnail_url
                     or lot.thumbnail_url)):
            e.queued_task = "inspect"
            db.commit()
            db.close()
            run_inspection(lot_db_id)
            return
        e.last_attempted_at = datetime.now(timezone.utc)

        try:
            with timed("enrich", "lot", auction_id=lot.auction_id,
                       label=(lot.title or "")[:60]) as ph:
                _enrich(lot, e, db, phase=ph)
                ph.add(1)
                ph.note(ai_source=e.ai_source, comps=e.comp_count,
                        price_source=(e.price_source or "")[:40])
            e.status = "success"
            e.error_message = None
        except Exception as exc:  # noqa: BLE001 — one lot failing must not kill a batch
            logger.warning("Enrichment failed for lot %s: %s", lot_db_id, exc)
            e.status = "failed"
            e.error_message = str(exc)
        e.progress = None
        db.commit()
        if e.status == "success":
            try:
                _verify_gold(db, lot, e)
            except Exception as exc:  # noqa: BLE001 — the audit must not fail the lot
                logger.warning("Gold check failed for lot %s: %s", lot_db_id, exc)
    finally:
        db.close()




@contextmanager
def _step(phase, name: str):
    """Time an inner step, or do nothing when no phase is recording."""
    if phase is None:
        yield
        return
    with phase.sub(name):
        yield


def apply_bolo_match(e: models.Enrichment, title: str, description: str,
                     protected=frozenset()) -> bool:
    """Write the BOLO fields onto an enrichment row. True if a brand matched.

    Pulled out of _enrich so IMPORT can call it too. The match is regex over
    the title and description: free, deterministic, and it needs nothing the
    import has not already fetched. That is what makes a BOLO-filtered
    import possible at all — the decision can be made before a cent is spent
    on the AI pass.

    Idempotent, so enrichment re-running over an already-matched row just
    writes the same values back.
    """
    match = bolo_matcher.match(title or "", description or "")
    if not match or "bolo_brand" in protected:
        return bool(match)
    e.bolo_brand = match["brand"]
    e.bolo_category = match["category"]
    if "bolo_tier" not in protected:
        e.bolo_tier = str(match["tier"]) if match["tier"] is not None else None
    e.bolo_confidence = match["confidence"]
    e.matched_model = match["matched_model"]
    e.target_buy_price = match["target_buy_high"]
    e.ship_class = match["ship_class"]
    # Broader than the BOLO file's own flag (tier-3 only): ANY luxury or
    # sneaker match needs authentication before its comps mean anything —
    # a $45 "Hublot" is a replica until proven otherwise.
    e.auth_required = bool(
        match.get("auth_required")
        or match.get("category") in {
            "luxury", "luxury_mid", "luxury_watch", "sneakers",
            "designer_eyewear", "premium_eyewear",
            # 14K/sterling values hinge on the metal being real —
            # the Watermark audit's top golds were unflagged jewelry.
            "precious_metals", "gold", "silver", "jewelry",
        }
    )
    return True


def _enrich(lot: models.Lot, e: models.Enrichment, db: Session,
            phase=None) -> None:
    title = lot.title or ""
    description = lot.description or ""
    # Fields the user hand-corrected are never overwritten by re-enrichment.
    protected = set(e.user_overrides or [])

    # --- 0. Boilerplate rows are not items ---
    # "More Lots Loading", "Pick Up Location" and kin get no AI call, no
    # comp lookup, and no number — every cent spent on one buys a confident
    # price for nothing. Marked success so bulk enrich never re-queues it.
    if pricing.is_placeholder_title(title):
        e.enriched_title = None
        e.verdict = None
        e.ai_source = "none"
        e.est_resale = None
        e.price_low = None
        e.price_high = None
        e.comp_count = 0
        e.comps = None
        e.price_source = "placeholder title — not an item, not priced"
        e.max_bid = None
        e.est_roi = None
        e.profit = None
        e.roi_status = None
        _progress(db, e, None)
        return

    # --- 1. BOLO match (free, deterministic) ---
    _progress(db, e, "matching against BOLO brand list…")
    with _step(phase, "bolo"):
        apply_bolo_match(e, title, description, protected)

    # --- 2. AI title + condition verdict ---
    # Unless the house already told us everything the model would: the retail
    # price is printed on the front of the title, and the condition grade
    # with it. Reading those costs nothing, so an entire Amazon overstock
    # catalogue enriches without a single model call.
    ai = None
    from_title = pricing.retail_from_title(title) is not None
    if from_title:
        _progress(db, e, "reading the price off the title…")
        if "enriched_title" not in protected:
            e.enriched_title = pricing.title_without_retail_prefix(title)[:80] or None
        if "verdict" not in protected:
            e.verdict = pricing.condition_from_title(title)
        e.confidence = "strong"
        e.ai_source = "title"
        if "notes" not in protected:
            e.notes = ("Retail price and condition read from the lot title — "
                       "no AI spend on this lot.")
    elif len(description.strip()) >= MIN_DESC_FOR_TEXT_PASS:
        _progress(db, e, "AI reading the description…")
        with _step(phase, "ai_text"):
            ai = _call_text(title, description)
        e.ai_source = "text"
    # The photo pass is the expensive one — never reach for it on a lot whose
    # price and condition were already free.
    if not from_title and (ai is None or not ai.get("confident")):
        _progress(db, e, "AI examining the photo…")
        with _step(phase, "image_download"):
            image_bytes = _download_image(lot.thumbnail_url or lot.hd_thumbnail_url)
        if image_bytes:
            with _step(phase, "ai_vision"):
                vision = _call_vision(title, image_bytes, description)
            if vision is not None and (ai is None or vision.get("confident")):
                ai = vision
                e.ai_source = "vision"
    if ai:
        if "enriched_title" not in protected:
            e.enriched_title = (ai.get("enriched_title") or "")[:80] or None
        if "verdict" not in protected:
            e.verdict = ai.get("verdict")
        e.confidence = "strong" if ai.get("confident") else "weak"
        if "notes" not in protected:
            e.notes = ai.get("notes")
        # The AI saw the actual item, so its ship-tier judgment beats the
        # title-regex guess from import (which can't tell a table from a
        # table lamp). Runs before ROI so the logistics penalty uses it.
        ship = (ai.get("ship") or "").upper()
        if ship in ("EASY", "NEUTRAL", "HARD") and "logistics_ease" not in protected:
            lot.logistics_ease = ship
            _mark_ai_ship(e)
    elif e.ai_source is None:
        e.ai_source = "none"

    # --- 3. Comps (skipped entirely when the user hand-set the resale) ---
    if "est_resale" not in protected:
        # Liquidation catalogues (Amazon returns/overstock) print the retail
        # price at the front of the title. That beats a comp search on this
        # kind of stock — the goods are new and generic, so keyword comps
        # come back full of unrelated listings — and it costs nothing.
        # Expensive claims get cross-checked though: printed MSRPs run
        # stale or inflated, and verified_title_price takes the lower of
        # claim and market when real money is at stake.
        _progress(db, e, "pricing from the title (checking big claims)…")
        # Set before the branch: the retail-title path never assigns it, and
        # the price log wants to know what was searched either way.
        search_title = e.enriched_title or title
        titled = pricing.verified_title_price(title, e.enriched_title)
        if titled:
            comps = titled
        else:
            _progress(db, e, "searching eBay for comparable sales…")
            with _step(phase, "comps"):
                comps = pricing.lookup_comps(search_title)
        mult = CONDITION_MULTIPLIER.get(e.verdict, 1.0)
        if not comps["est_resale"] and e.est_resale is not None:
            # The lookup came back empty, and we already had a number.
            # Every failure path in lookup_comps - no key, breaker open,
            # quota wall, non-200 - returns exactly this shape, which is
            # indistinguishable from "nothing like this has ever sold".
            # Overwriting a working estimate on that basis is how one
            # re-price during an outage erased values: a DeWalt DCF825 went
            # from $19.49 to nothing in a single click. Keep what we had,
            # and leave a trace so the trail can finally tell "no sold
            # history" apart from "the API returned nothing".
            price_log.record(
                lot.id, None, method="empty", chosen=False,
                note=(f"lookup returned nothing; kept "
                      f"${float(e.est_resale):.2f} "
                      f"({e.price_source or 'unknown source'})"),
                query=search_title)
        else:
            e.est_resale = (round(float(comps["est_resale"]) * mult, 2)
                            if comps["est_resale"] else None)
            e.price_low = (round(float(comps["price_low"]) * mult, 2)
                           if comps["price_low"] else None)
            e.price_high = (round(float(comps["price_high"]) * mult, 2)
                            if comps["price_high"] else None)
            e.comp_count = comps["comp_count"]
            e.price_source = comps["price_source"]
            if e.est_resale is not None:
                price_log.record(lot.id, float(e.est_resale), method="comps",
                                 price_source=e.price_source,
                                 comp_count=e.comp_count, query=search_title)
            # .get: the retail-in-title path predates the comps key on some
            # builders — missing simply means no evidence rows to show.
            e.comps = comps.get("comps") or None
            if mult != 1.0 and comps["price_source"]:
                e.price_source += f" ×{mult:g} condition"
            _apply_estimate_cap(lot, e)

    # --- 4. ROI ---
    _progress(db, e, "computing max bid and ROI…")
    _apply_roi(lot, e)


def _apply_roi(lot: models.Lot, e: models.Enrichment) -> None:
    """ROI verdict from whatever est_resale is currently on the enrichment.
    Always compute the ceiling when we have a resale estimate — even on
    red-flagged lots, knowing max_bid is useful context. Red flags and
    unreachable pickups just can't be GOLD MINEs."""
    red_flag = e.verdict in ("broken, damaged, or for parts",
                             "untested or unknown condition")
    if e.est_resale:
        # Everything logistics costs on this lot: freight in from the
        # auction house, plus packing and fee drag on the way back out.
        penalty = (LOGISTICS_COST.get(lot.logistics_ease or "NEUTRAL", 6.50)
                   + _inbound_shipping(lot))
        effective_bid = float(max(lot.current_bid or 0, lot.next_bid or 0,
                                  MIN_ASSUMED_BID))
        # Use the auction house's real premium when we know it (18% houses
        # were being graded at the 15% default).
        mult = getattr(lot.auction, "buyer_premium_mult", None) if lot.auction else None
        premium = (float(mult) - 1) if mult else financials.BUYERS_PREMIUM
        lead = financials.evaluate_lead(
            resale_value=float(e.est_resale),
            current_bid=effective_bid,
            logistics_penalty=penalty,
            dts=0.0,  # no sell-through data yet — don't fail lots on it
            buyers_premium=premium,
        )
        e.max_bid = lead.max_bid
        e.est_roi = lead.roi
        # The all-in number the ROI is actually computed against: hammer +
        # premium + tax + freight in, packing and fee drag. Stored so the UI can
        # show WHY a $15 "est cost" item returns 12% and not 300%.
        e.all_in_cost = lead.total_cost
        # One listing's asking price isn't evidence — every wrong gold mine
        # in the Watermark audit ($2 bills "worth" $260, a $487 pearl ring)
        # traced to a single generic active comp. The numbers stay for
        # context; the GOLD MINE badge requires at least 2 agreeing comps.
        # A retail price printed in the lot title is the exception: it's one
        # data point but an authoritative one, not a hopeful asking price.
        # A titled vehicle can price out "profitably" ($5,679 max on a
        # $12,500 school bus, math intact) and still be no part of this
        # business — DMV paperwork, not a parcel. Gate, don't delete: the
        # numbers stay visible, the badge never mints, the audit never
        # spends on it.
        titled_vehicle = (settings_store.flag(
                              "exclude_titled_vehicles",
                              settings_store.EXCLUDE_VEHICLES_DEFAULT)
                          and pricing.is_titled_vehicle(lot.title or "",
                                                        lot.category))
        from_title = (e.price_source or "").startswith("retail $")
        # An audit-corrected value is the auditor's own appraisal — a
        # deliberate second opinion outranks the comp count that priced the
        # number it replaced. A CONFIRMED value has the same standing: the
        # auditor is the second agreeing source the thin gate demands, and
        # any value change clears gold_check, so the exemption can't
        # outlive the number it vouched for.
        audit_backed = ((e.price_source or "").startswith("audit-corrected")
                        or e.gold_check in ("confirmed", "corrected"))
        thin_evidence = ((e.comp_count or 0) < 2
                         and not (from_title or audit_backed))
        e.profit = lead.profit
        # An audit demotion stands until the VALUE changes (which clears
        # gold_check) — recomputing ROI on a new bid or a reprice must not
        # resurrect the badge. Without this, every hourly bid refresh
        # re-minted golds the audit had already rejected.
        demoted = getattr(e, "gold_check", None) == "demoted"
        e.roi_status = ("PASS" if (red_flag or lot.unreachable_pickup
                                   or titled_vehicle
                                   or thin_evidence or demoted)
                        else lead.status)
        # Name the gate, in gate order — the first one that blocked is the
        # one the user should read. NULL on a clean gold: nothing to explain.
        if red_flag:
            e.roi_reason = f"condition red flag: {e.verdict}"
        elif lot.unreachable_pickup:
            e.roi_reason = "pickup-only and outside your radius"
        elif titled_vehicle:
            e.roi_reason = "titled vehicle — excluded by your settings"
        elif thin_evidence:
            n = e.comp_count or 0
            e.roi_reason = (f"only {n} comp{'s' if n != 1 else ''} — "
                            "the badge needs 2 agreeing")
        elif demoted:
            e.roi_reason = "audit demoted the value (see its note)"
        elif e.roi_status == "PASS":
            if lead.max_bid <= 0:
                e.roi_reason = "value too low to clear costs at any bid"
            else:
                e.roi_reason = (f"bid ${effective_bid:.0f} already past the "
                                f"${float(lead.max_bid):.0f} ceiling")
        else:
            e.roi_reason = None
    else:
        # No usable price means no usable verdict. Leaving the previous run's
        # numbers in place is how a lot whose comps were just rejected stayed
        # the app's top "gold mine" at $3252 profit.
        e.max_bid = None
        e.profit = None
        e.est_roi = None
        e.roi_status = "PASS" if (red_flag or lot.unreachable_pickup) else None
        e.roi_reason = (f"condition red flag: {e.verdict}" if red_flag
                        else "pickup-only and outside your radius"
                        if lot.unreachable_pickup else "no resale value found")


GOLD_CHECK_PROMPT = """You are auditing a resale-auction buying decision. Be skeptical.

Lot title: {title}
Description: {description}
Identified as: {enriched_title}
Condition verdict: {verdict}
Claimed resale value: ${est_resale} — source: {price_source} ({comp_count} comps)
House estimate: {house_estimate}
Current bid ${bid}; the app would bid up to ${max_bid}.

Is ${est_resale} a realistic eBay SOLD price for this EXACT item in this
condition{photo_note}? Frequent mistakes to catch: comps that matched a
different or premium product; costume jewelry priced as fine jewelry, or
plate priced as sterling; broken/for-parts items priced as working; one
item carrying a whole-pile total; hype or asking prices nowhere near what
actually sells.

Return ONLY valid JSON:
{{"plausible": true or false, "reason": string, "realistic_value": number}}
reason = one short sentence a reseller can act on.
realistic_value = YOUR estimate of what this exact item in this condition
actually sells for on eBay — a single number (the midpoint if you'd give a
range), 0 if it has no meaningful resale value. Required when plausible is
false: it replaces the claim you rejected."""


# A dollar figure in prose: "$80-150", "$80 - $150", "around $90".
_PROSE_VALUE_RE = re.compile(r"\$\s?(\d[\d,]*(?:\.\d{1,2})?)")


def _audit_correction(result: dict, claimed: float) -> float | None:
    """The auditor's own value, when usable: a positive number meaningfully
    below the claim it just rejected. Zero means 'no real value' (plain
    demotion says that better), and anything at or above the claim is
    incoherent with calling the claim implausible.

    Falls back to reading the number out of the reason text. The model
    regularly names a figure in prose and leaves realistic_value empty -
    a Swarovski member gift was rejected with "typically sell for $80-150"
    and still displayed the debunked $370, because the structured field
    was missing and the code would not guess. It does not have to guess:
    the number is right there in the sentence it just wrote.

    The first figure below the claim wins, which is deliberately the LOW
    end of a range. An auditor rejecting a value for being too high should
    not have its correction rounded up, and the reason usually restates the
    rejected figure too - excluded by the same below-the-claim rule.
    """
    try:
        value = float(result.get("realistic_value"))
    except (TypeError, ValueError):
        value = None
    if value is None:
        for match in _PROSE_VALUE_RE.finditer(str(result.get("reason") or "")):
            try:
                candidate = float(match.group(1).replace(",", ""))
            except ValueError:
                continue
            if 0 < candidate < claimed:
                value = candidate
                break
    if value is None or not 0 < value < claimed:
        return None
    return round(value, 2)


def _verify_gold(db: Session, lot: models.Lot, e: models.Enrichment, *,
                 candidate: bool = False) -> None:
    """Second-opinion audit on a fresh GOLD MINE — or, with candidate=True,
    on a thin-evidence lot the sweep wants a verdict on.

    Confirmed golds keep the badge (plus a ✓ in the UI), and a confirmed
    CANDIDATE is promoted to it: comp-less values come from the itemized
    vision pass summing what it reads in the photo, and the thin gate
    blocked every one of them no matter how good — 529 of 731 Vinted
    media piles, including an $8 lot the inspection priced at $225. The
    auditor is the second source of evidence the badge demands. An
    implausible value is REPLACED by the auditor's own estimate and the
    lot regraded on it — the audit note already said what the item really
    sells for, and discarding that left debunked numbers on display with
    no ROI signal. Only when the auditor offers no usable number does the
    lot fall to a plain demotion. Fail-open: if the call fails the lot
    stands unchanged, so the next sweep retries it. Hand-set prices are
    never second-guessed."""
    if not GOLD_CHECK or e.gold_check:
        return
    if e.roi_status != "GOLD MINE" and not candidate:
        return
    if "est_resale" in set(e.user_overrides or []):
        return
    # House rule: never hold a transaction across the network.
    db.commit()
    image_bytes = _download_image(lot.thumbnail_url or lot.hd_thumbnail_url)
    prompt = GOLD_CHECK_PROMPT.format(
        title=lot.title or "",
        description=(lot.description or "")[:800] or "(none)",
        enriched_title=e.enriched_title or "(none)",
        verdict=e.verdict or "(none)",
        est_resale=e.est_resale,
        price_source=e.price_source or "?",
        comp_count=e.comp_count or 0,
        house_estimate=(f"${float(lot.estimate_low):g}-${float(lot.estimate_high):g} "
                        f"(the auctioneer's own range — promotional, not market data)"
                        if getattr(lot, "estimate_low", None) else "(none given)"),
        bid=lot.current_bid or 0,
        max_bid=e.max_bid or 0,
        photo_note=", shown in the photo" if image_bytes else "")
    content = [{"type": "text", "text": prompt}]
    if image_bytes:
        content.insert(0, {"type": "image",
                           "source": {"type": "base64",
                                      "media_type": _image_mime(image_bytes),
                                      "data": base64.b64encode(image_bytes).decode()}})
    result = _call_with_retry(lambda: client.messages.create(
        model=MODEL, max_tokens=250,
        messages=[{"role": "user", "content": content}]).content[0].text)
    if result is None or not isinstance(result.get("plausible"), bool):
        return
    reason = str(result.get("reason") or "")[:300]
    if result["plausible"]:
        e.gold_check = "confirmed"
        e.gold_check_note = reason or None
        # A confirmed candidate earns the badge: 'confirmed' now exempts
        # the thin gate in _apply_roi, so the promotion survives every
        # later regrade and bid refresh too.
        if candidate:
            _apply_roi(lot, e)
    else:
        e.gold_check_note = reason or "resale value judged implausible"
        if e.est_resale is not None:
            price_log.record(lot.id, float(e.est_resale), method="comps",
                             price_source=e.price_source,
                             comp_count=e.comp_count, chosen=False,
                             rejected=True,
                             note=f"audit rejected: {reason}"[:400])
        corrected = _audit_correction(result, float(e.est_resale))
        if corrected is not None:
            e.gold_check = "corrected"
            # Both numbers are worth keeping: the gap between what the
            # comps claimed and what the auditor believed is the measure
            # of how far the comp search drifted.
            price_log.record(lot.id, float(e.est_resale), method="comps",
                             price_source=e.price_source,
                             comp_count=e.comp_count, chosen=False,
                             rejected=True, note=f"audit: {reason}"[:400])
            price_log.record(lot.id, corrected, method="audit",
                             price_source="audit-corrected",
                             note=reason or None)
            e.price_source = (f"audit-corrected "
                              f"(comps said ${float(e.est_resale):g})")
            e.est_resale = corrected
            # Regrade on the corrected number: 'corrected' passes the
            # demotion gate, and the guard above means no second audit —
            # this value IS the audit's.
            _apply_roi(lot, e)
        else:
            e.gold_check = "demoted"
            e.roi_status = "PASS"
    db.commit()


INSPECT_PROMPT = """This is a photo of a multi-item auction lot titled: {title}

The listing description (may be empty or boilerplate — trust the photo over
it, but use its sizes, model numbers, and quantities):
{description}

Identify each INDIVIDUALLY SELLABLE item you can actually read or recognize in
the photo — CD/DVD/book spines, game boxes, branded products, etc. For each,
give an eBay-searchable title (under 60 chars) AND your estimate of what that
item actually SELLS for secondhand in visible condition — a realistic eBay
sold price, not an asking price and not retail. Title items the way buyers
SEARCH: lead with the brand/franchise collectors type ("Ty Beanie Baby Peanut
Elephant Light Blue", not "vintage elephant plush"). For apparel/shoes, ALWAYS
carry the audience and size when shown (kids/youth/toddler/boys/girls/men's/
women's, size) — a kids item priced against adult listings is a wrong price.
For collectibles with famous rare variants (Beanie Babies, coins, cards):
price the EXACT variant you can see — color and tag generation matter — and
assume the COMMON variant unless the rare one's features are clearly
confirmed. Mass-produced 90s collectibles (Beanie Babies, Precious Moments,
Boyds) really sell for $5-25 each despite the legends; a light blue Peanut
is a $10 elephant, only the royal blue one is the famous one.
Skip anything you can't specifically identify — never guess or pad the list.
If the photo shows ONE cohesive product (a single bracelet, one appliance,
one doll), return it as exactly ONE item — never split its components
(charms on a bracelet, pieces of a chess set, keys on a keyboard) into
separate entries.
Max 12 items.

Return ONLY valid JSON:
{{"items": [{{"title": string, "est_value": number or null}}], "summary": string, "ship": string}}
est_value = realistic secondhand SOLD price in USD; null only if you genuinely can't estimate.
summary = one sentence on what the lot contains overall.
ship = how hard the WHOLE lot is to ship, exactly one of "EASY" (fits a padded mailer or small box) | "NEUTRAL" (normal parcel or two) | "HARD" (furniture, appliance, oversized/freight)
"""

MAX_INSPECT_ITEMS = 12

# The smallest item worth listing on its own. Summing every item in a mixed
# lot flatters it badly: thirty things at $12 totals $360, but that is thirty
# photographs, thirty listings and thirty parcels for $12 apiece. Nobody
# works that lot, so it should not outrank a single $300 item.
MIN_ITEM_VALUE = float(os.environ.get("MIN_ITEM_VALUE", "20"))

# What everything under that threshold is worth together — sold as one
# bundle, which is how filler actually moves, rather than individually or
# not at all. 0 disables the credit entirely.
FILLER_REALIZATION = float(os.environ.get("FILLER_REALIZATION", "0.2"))

# How far below MIN_ITEM_VALUE the vision pass must put an item before its
# comp lookup is skipped entirely. 1.5 means "confidently filler": a $20
# floor skips lookups only for items guessed under $30, so anything near
# the line still gets real market data.
FILLER_SKIP_MARGIN = float(os.environ.get("FILLER_SKIP_MARGIN", "1.5"))


def run_inspection(lot_db_id: int) -> None:
    """Itemized vision pass for mixed lots ("Lot of 10 CDs"): read the photo,
    identify each sellable item, price them individually, and total it up.
    Called from POST /lots/{id}/inspect as a background task."""
    db: Session = SessionLocal()
    try:
        lot = db.query(models.Lot).filter(models.Lot.id == lot_db_id).first()
        if not lot:
            return
        e = lot.enrichment
        # Same contract as run_enrichment: cancelling flips queued lots back
        # to 'pending', so anything no longer queued was cancelled before its
        # turn came up (this matters for resumed orphans, which can wait a
        # while — the live path runs within seconds of being queued).
        if e.status != "queued":
            return
        e.last_attempted_at = datetime.now(timezone.utc)
        try:
            _inspect(lot, e, db)
            e.status = "success"
            e.error_message = None
        except Exception as exc:  # noqa: BLE001
            logger.warning("Inspection failed for lot %s: %s", lot_db_id, exc)
            e.status = "failed"
            e.error_message = str(exc)
        e.progress = None
        db.commit()
        if e.status == "success":
            try:
                _verify_gold(db, lot, e)
            except Exception as exc:  # noqa: BLE001 — the audit must not fail the lot
                logger.warning("Gold check failed for lot %s: %s", lot_db_id, exc)
    finally:
        db.close()


def _inspect(lot: models.Lot, e: models.Enrichment, db: Session) -> None:
    # Full-size image beats the thumbnails for reading spines/labels
    _progress(db, e, "downloading the full-size photo…")
    image_bytes = _download_image(lot.fullsize_url or lot.hd_thumbnail_url
                                  or lot.thumbnail_url)
    if not image_bytes:
        raise RuntimeError("no image available for inspection")

    _progress(db, e, "AI identifying each item in the photo…")
    result = _vision_json(
        INSPECT_PROMPT.format(
            title=lot.title or "",
            description=(lot.description or "")[:1500] or "(none)"),
        image_bytes, max_tokens=800)
    if result is None:
        raise RuntimeError("vision call failed")

    items = (result.get("items") or [])[:MAX_INSPECT_ITEMS]
    lines = []
    keepers: list[float] = []   # items worth listing on their own
    filler_total = 0.0          # everything under MIN_ITEM_VALUE, bundled
    filler_max = 0.0
    priced = 0           # comp-priced KEEPERS — what the lot's value rests on
    ai_priced = 0
    filler = 0
    skipped_lookups = 0
    for i, item in enumerate(items, 1):
        item_title = (item.get("title") or "").strip()
        if not item_title:
            continue
        _progress(db, e, f"pricing item {i}/{len(items)}: {item_title[:40]}…")
        # Skip the comp lookup when the vision pass has already said this is
        # filler. Anything under MIN_ITEM_VALUE gets swept into one bundled
        # figure regardless, so comps bought here are paid for and thrown
        # away - and a box lot can hold twelve of them, each walking up to
        # four query variants.
        #
        # The guess has to be confidently low to skip: the threshold is
        # MIN_ITEM_VALUE with headroom, so an item the model puts near the
        # line still gets real comps. AI estimates skew optimistic, so a
        # low guess is the safe direction to trust.
        guess = _sane_estimate(item.get("est_value"))
        # "Even allowing generous headroom on an optimistic guess, this is
        # still filler." Comparing the raw guess to the floor would skip an
        # item the model puts at $25 - which could comp at $40 and deserve
        # its own listing. Discount it the way the no-comps path would,
        # then give it the margin, and only skip what clears neither.
        skip_ceiling = (guess * AI_ESTIMATE_REALIZATION * FILLER_SKIP_MARGIN
                        if guess is not None else None)
        if skip_ceiling is not None and skip_ceiling < MIN_ITEM_VALUE:
            value = round(guess * AI_ESTIMATE_REALIZATION, 2)
            filler_total += value
            filler_max = max(filler_max, value)
            filler += 1
            skipped_lookups += 1
            lines.append(f"{item_title} → ${value} (below the listing floor, "
                         f"not comped) · filler")
            continue
        comps = pricing.lookup_comps(item_title)
        if comps["est_resale"]:
            # Real market data always wins over the model's guess.
            value, tag = float(comps["est_resale"]), f"{comps['comp_count']} comps"
            from_comps = True
        else:
            # No comps — fall back to the AI's own sold-price estimate from
            # the same vision call, discounted because model guesses skew
            # optimistic.
            ai_val = (_sane_estimate(item.get("est_value"))
                      if AI_ESTIMATE_REALIZATION > 0 else None)
            if ai_val is None:
                lines.append(f"{item_title} → no comps")
                continue
            value = round(ai_val * AI_ESTIMATE_REALIZATION, 2)
            tag = "AI estimate, no comps"
            from_comps = False
        if value < MIN_ITEM_VALUE:
            # Filler. Counted as part of one bundled sale, not as its own
            # listing — otherwise a crate of $12 oddments outranks a real item.
            filler_total += value
            filler_max = max(filler_max, value)
            filler += 1
            lines.append(f"{item_title} → ${value} ({tag}) · filler")
            continue
        keepers.append(value)
        if from_comps:
            priced += 1
        else:
            ai_priced += 1
        lines.append(f"{item_title} → ${value} ({tag})")

    # A single cohesive product the model split anyway (7 charms of one
    # bracelet, $295 "total" for a $60 bracelet) must not be summed: the
    # biggest component's value stands in for the whole item. Multi-item
    # titles keep the sum, but keepers past the best one carry the part-out
    # discount — each extra item is another listing, fee, and parcel.
    single_product = (not looks_multi_item(lot.title)
                      and (len(keepers) + filler) > 1)
    if single_product:
        best = max(keepers) if keepers else filler_max
        total = best
        lines.append(f"[single product — largest component value ${best:g} "
                     f"stands for the whole item; components are not summed]")
    else:
        bundled = round(filler_total * FILLER_REALIZATION, 2)
        if filler:
            lines.append(f"[{filler} items under ${MIN_ITEM_VALUE:g} → "
                         f"${bundled} bundled, from ${round(filler_total, 2)} summed]")
        keepers.sort(reverse=True)
        partout = round(sum(keepers[1:]) * PARTOUT_REALIZATION, 2)
        if len(keepers) > 1:
            lines.append(f"[{len(keepers) - 1} smaller keeper(s) "
                         f"×{PARTOUT_REALIZATION:g} part-out → ${partout}, "
                         f"from ${round(sum(keepers[1:]), 2)} summed]")
        total = (keepers[0] if keepers else 0.0) + partout + bundled

    protected = set(e.user_overrides or [])
    summary = result.get("summary") or ""
    # Inspection must never replace stronger evidence with weaker: a lot
    # already priced from more real comps than the itemized pass found keeps
    # its price (and its gold status) — the breakdown is still recorded.
    # Inspecting a strong 18-comp gold item used to swap in a 0-comp AI
    # guess, which the thin-evidence rule then demoted.
    prior_comps = e.comp_count or 0
    keep_prior = (e.est_resale is not None
                  and not (e.price_source or "").startswith("itemized")
                  and prior_comps > priced)
    kept = " · kept pre-inspection price (stronger comps)" if keep_prior else ""
    if "notes" not in protected:
        head = (f"[inspected: {len(items)} items, {priced} comp-priced, "
                f"{ai_priced} AI-estimated")
        if filler:
            head += f", {filler} filler under ${MIN_ITEM_VALUE:g}"
        if skipped_lookups:
            head += f" ({skipped_lookups} not comped)"
        e.notes = f"{head}{kept}] {summary}\n" + "\n".join(lines)
    e.ai_source = "vision-itemized"
    # Vision saw the whole lot — trust its ship-tier call over the title regex.
    ship = (result.get("ship") or "").upper()
    if ship in ("EASY", "NEUTRAL", "HARD") and "logistics_ease" not in protected:
        lot.logistics_ease = ship
        _mark_ai_ship(e)
    if "est_resale" not in protected:
        if keep_prior:
            _apply_roi(lot, e)     # price stays; bid/ship may have moved
        elif total > 0:
            e.est_resale = round(total, 2)
            price_log.record(lot.id, round(total, 2), method="itemized",
                             price_source="itemized vision",
                             comp_count=priced,
                             note=f"{len(items)} items, {filler} filler")
            e.price_low = None
            e.price_high = None
            e.comp_count = priced          # real comps only — AI guesses don't count
            if single_product:
                e.price_source = (f"itemized vision (single product — largest "
                                  f"of {len(items)} components, not summed)")
            else:
                bits = [f"{priced} from comps"] if priced else []
                if ai_priced:
                    bits.append(f"{ai_priced} AI-estimated ×{AI_ESTIMATE_REALIZATION:g}")
                if len(keepers) > 1:
                    bits.append(f"part-out ×{PARTOUT_REALIZATION:g}")
                if filler:
                    bits.append(f"{filler} filler bundled ×{FILLER_REALIZATION:g}")
                sellable = priced + ai_priced
                e.price_source = (f"itemized vision ({' + '.join(bits) or 'nothing priced'}"
                                  f" — {sellable} of {len(items)} worth listing)")
            _apply_estimate_cap(lot, e)
            _apply_roi(lot, e)


# ------------------------------------------------------------------ AI calls

def _call_with_retry(make_call) -> dict | None:
    """make_call returns the model's TEXT (any provider); this parses JSON
    out of it, retrying transport and parse failures alike."""
    last: Exception | None = None
    for _ in range(MAX_ATTEMPTS):
        try:
            return _parse_json_response(make_call())
        except Exception as exc:  # noqa: BLE001
            last = exc
    logger.warning("AI call failed after %s attempts: %s", MAX_ATTEMPTS, last)
    return None


def _call_text(title: str, description: str) -> dict | None:
    return _call_with_retry(lambda: client.messages.create(
        model=MODEL,
        max_tokens=400,
        messages=[{"role": "user",
                   "content": TEXT_PROMPT.format(title=title,
                                                 description=description[:2000])}],
    ).content[0].text)


def _image_mime(image_bytes: bytes) -> str:
    """Sniff the real format from magic bytes. HiBid serves JPEG, but
    Vinted serves webp — and a mislabeled media_type is a rejected API
    call, which fail-open turns into a silently blind vision pass."""
    if image_bytes[:4] == b"RIFF" and image_bytes[8:12] == b"WEBP":
        return "image/webp"
    if image_bytes[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if image_bytes[:4] == b"GIF8":
        return "image/gif"
    return "image/jpeg"


def _vision_json(prompt: str, image_bytes: bytes, max_tokens: int) -> dict | None:
    """One dispatcher for every image-understanding call. Provider comes
    from config: Gemini when its key is set (it identified the user's
    jewelry lots better in practice), Claude otherwise, VISION_PROVIDER to
    force either. The gold-check audit is deliberately NOT routed through
    here — a value priced by one model and audited by another is a
    genuinely independent second opinion."""
    mime = _image_mime(image_bytes)
    if config.vision_provider() == "gemini":
        return _call_with_retry(
            lambda: gemini.generate(prompt, image_bytes, max_tokens=max_tokens,
                                    mime_type=mime))
    b64 = base64.b64encode(image_bytes).decode()
    return _call_with_retry(lambda: client.messages.create(
        model=MODEL,
        max_tokens=max_tokens,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64",
                                             "media_type": mime,
                                             "data": b64}},
                {"type": "text", "text": prompt},
            ],
        }],
    ).content[0].text)


def _call_vision(title: str, image_bytes: bytes, description: str = "") -> dict | None:
    return _vision_json(
        VISION_PROMPT.format(title=title,
                             description=(description or "")[:1500] or "(none)"),
        image_bytes, max_tokens=400)


def _download_image(url: str | None) -> bytes | None:
    """HiBid's CDN requires a Referer header; Anthropic's server-side fetcher
    won't send one, so we download the bytes ourselves and upload base64."""
    if not url:
        return None
    try:
        r = httpx.get(url, timeout=20.0, follow_redirects=True, headers={
            "Referer": "https://hibid.com/",
            "User-Agent": config.HIBID_USER_AGENT,
        })
        if r.status_code == 200 and len(r.content) > 1000:
            return r.content
    except Exception:
        pass
    return None


# How many regraded rows ride in one transaction. One commit for the whole
# run meant one loser: a regrade's single end-of-run commit lost a race with
# a reprice committing the same enrichment rows per-lot, and all ~1,200
# verdicts silently reverted (81 stayed wrong until a manual re-run). Small
# batches make a conflict cost one batch, not the run.
REGRADE_BATCH = int(os.environ.get("REGRADE_BATCH", "100"))


def _regrade_rows(db: Session, rows: list, job: str | None = None) -> tuple[int, int]:
    """The regrade loop, committing every REGRADE_BATCH rows.

    A batch whose commit fails is rolled back, LOGGED, and skipped — the
    rows keep their old verdicts and the run moves on. Returns
    (verdicts changed and committed, rows lost to failed commits)."""
    changed = lost = 0
    batch_changed = 0
    batch_start = 1
    for i, lot in enumerate(rows, 1):
        e = lot.enrichment
        # Hand-corrected PRICES are protected, but the verdict derived
        # from them still follows the current ROI target.
        before = e.roi_status
        _apply_roi(lot, e)
        if e.roi_status != before:
            batch_changed += 1
        if i % REGRADE_BATCH == 0 or i == len(rows):
            try:
                db.commit()
                changed += batch_changed
            except Exception as exc:  # noqa: BLE001 — a lost batch must not sink the run, or vanish
                db.rollback()
                lost += i - batch_start + 1
                logger.warning(
                    "Regrade commit failed for rows %s-%s (%s rows keep "
                    "their old verdicts): %s",
                    batch_start, i, i - batch_start + 1, exc)
            batch_changed = 0
            batch_start = i + 1
        if job and i % 200 == 0:
            jobs.update(job, current=i)
    return changed, lost


def run_regrade(resume_job_id: str | None = None) -> None:
    """Recompute ROI verdicts ONLY — no comp lookups, no AI, no network.

    Changing the target ROI doesn't change what an item is worth, just
    whether its current bid still clears the bar, so max_bid/profit/
    est_roi/roi_status are pure arithmetic over values already stored.
    Seconds, not the ~30 minutes a full reprice spends re-querying eBay.
    (Use run_reprice when the PRICING rules changed and the resale
    estimates themselves need rebuilding.)
    """
    db: Session = SessionLocal()
    # resume_job_id: the API enqueued a row and the worker claimed it, so
    # adopt that one instead of registering a second.
    job = resume_job_id or jobs.start(
        "regrade", "Re-grading items at the new ROI target")
    changed = lost = 0
    try:
        rows = (db.query(models.Lot)
                  .join(models.Enrichment)
                  .options(joinedload(models.Lot.enrichment))
                  .filter(models.Enrichment.est_resale.isnot(None))
                  .all())
        jobs.update(job, total=len(rows))
        changed, lost = _regrade_rows(db, rows, job)
        jobs.update(job, current=len(rows))
    finally:
        jobs.finish(job)
        db.close()
    tail = f" — {lost} rows LOST to failed commits, see warnings" if lost else ""
    print(f"Re-grade complete: {changed} verdicts changed{tail}")


def run_audit_sweep(resume_job_id: str | None = None) -> None:
    """Second-opinion audit for every gold the checker never saw.

    Regrades and bid refreshes mint golds by pure arithmetic — no AI — so a
    lot can carry the badge without _verify_gold ever seeing it (Bacliff
    came out of a full reprice with 15 of its 19 golds unaudited). This
    sweeps them: every visible GOLD MINE in an open auction with no
    gold_check gets the same audit a fresh gold gets at enrichment.
    Idempotent — audited lots drop out of the query — so a resume after a
    deploy simply re-queries what's left."""
    db: Session = SessionLocal()
    job = resume_job_id or jobs.start("audit-golds", "Auditing unchecked golds")
    audited = 0
    candidates: list = []
    try:
        base = (db.query(models.Lot)
                  .join(models.Enrichment)
                  .options(joinedload(models.Lot.enrichment))
                  .join(models.Auction, models.Lot.auction_id == models.Auction.id)
                  .filter(models.Enrichment.gold_check.is_(None),
                          or_(models.Lot.hidden.is_(False),
                              models.Lot.hidden.is_(None)),
                          (models.Auction.closing_date.is_(None))
                          | (models.Auction.closing_date >= datetime.now())))
        golds = base.filter(
            models.Enrichment.roi_status == "GOLD MINE").all()
        # Promotion candidates: blocked ONLY by thin evidence, with enough
        # profit on the table to be worth half a cent. Comp-less values
        # come from the itemized vision pass; the auditor is the second
        # agreeing source the thin gate demands.
        candidates = base.filter(
            models.Enrichment.roi_status == "PASS",
            models.Enrichment.roi_reason.like("only %"),
            models.Enrichment.profit >= PROMOTE_MIN_PROFIT).all()
        rows = [(lot, False) for lot in golds] + [(lot, True)
                                                  for lot in candidates]
        jobs.update(job, total=len(rows))
        for i, (lot, is_candidate) in enumerate(rows, 1):
            if jobs.is_cancelled(job):
                print(f"Audit sweep cancelled after {i - 1} lots")
                break
            try:
                _verify_gold(db, lot, lot.enrichment, candidate=is_candidate)
                audited += 1
            except Exception as exc:  # noqa: BLE001 — one lot must not stop the sweep
                logger.warning("Audit sweep failed for lot %s: %s", lot.id, exc)
            jobs.update(job, current=i, detail=(lot.title or "")[:40])
    finally:
        jobs.finish(job)
        db.close()
    print(f"Audit sweep complete: {audited} lots checked "
          f"({len(candidates)} thin candidates)")


# How many comp lookups a re-price keeps in flight at once. The wait per lot
# is almost entirely SoldComps' own latency - its endpoint scrapes eBay live,
# 5-8s a call - so overlapping the calls is where the time is. Four in
# flight at ~5s each is well under the process-wide throttle (SOLDCOMPS_RPS).
REPRICE_CONCURRENCY = int(os.environ.get("REPRICE_CONCURRENCY", "4"))


def _plan_reprice_lot(db: Session, lot_db_id: int) -> dict:
    """Phase A of a re-price: everything that needs the session and nothing
    that needs the network. Returns what to do next for this lot."""
    lot = db.query(models.Lot).filter(models.Lot.id == lot_db_id).first()
    e = lot.enrichment if lot else None
    plan = {"lot_db_id": lot_db_id, "title": (lot.title if lot else None)}
    if e is None:
        plan["kind"] = "missing"
        return plan
    marks = set(e.user_overrides or [])
    if "est_resale" in marks:
        plan["kind"] = "override"       # never overwrite a hand-corrected price
        return plan
    # Reclassify ship tier with the current regex rules — free, and ship-rule
    # fixes should reach old lots the same way pricing-rule fixes do.
    # Hand-set tiers ("logistics_ease") and AI-set tiers survive.
    if not marks & {"logistics_ease", "logistics_ease_ai"}:
        lot.logistics_ease = classify_logistics(
            lot.title or "", lot.category or "", lot.description or "")
    lot_title = lot.title or ""
    plan.update(title=lot_title, search_title=e.enriched_title or lot_title,
                retail=pricing.retail_from_title(lot_title), marks=marks)
    if plan["retail"] is not None and plan["retail"] < pricing.RETAIL_VERIFY_MIN:
        # Cheap claim: the zero-network path stands. The halved retail
        # already carries the discount, so the stale AI verdict must not be
        # applied on top of it — a $729 shelf came back at $255 (x0.5 x0.7)
        # instead of $364. Re-read the grade from the same title.
        plan["kind"] = "title"
        plan["comps"] = pricing.price_from_title(lot_title)
        if "verdict" not in marks:
            e.verdict = pricing.condition_from_title(lot_title)
    else:
        plan["kind"] = "network"
    return plan


def _fetch_reprice_comps(plan: dict) -> dict:
    """Phase B: the network call. Runs off the main thread with NO session -
    a comp lookup walks several query variants at up to 40s each, and holding
    an open transaction through that is what starved the pool once. Expensive
    retail claims come here too; their market cross-check is a lookup."""
    if plan["retail"] is not None:
        return pricing.verified_title_price(plan["title"], plan["search_title"])
    return pricing.lookup_comps(plan["search_title"])


def _apply_reprice(db: Session, plan: dict, comps) -> bool:
    """Phase C: write one result, on the main thread. Returns True when the
    lot was priced (or deliberately kept), False when there was nothing to
    apply - the lot vanished mid-run, or its lookup failed."""
    lot = db.query(models.Lot).filter(models.Lot.id == plan["lot_db_id"]).first()
    e = lot.enrichment if lot else None
    if e is None:
        return False            # deleted while we were off querying comps
    if plan["kind"] == "network" and plan["retail"] is not None \
            and "verdict" not in plan["marks"]:
        e.verdict = pricing.condition_from_title(plan["title"])
    if comps is None:
        return False
    search_title = plan["search_title"]
    prior_resale = e.est_resale
    mult = CONDITION_MULTIPLIER.get(e.verdict, 1.0)
    if not comps["est_resale"] and e.est_resale is not None:
        # Same guard as the comps block in _enrich, for the same reason:
        # every failure path in lookup_comps returns this shape, and a bulk
        # re-price during an outage would erase every value it touched.
        price_log.record(
            lot.id, None, method="empty", chosen=False,
            note=(f"reprice lookup returned nothing; kept "
                  f"${float(e.est_resale):.2f} "
                  f"({e.price_source or 'unknown source'})"),
            query=search_title)
    else:
        e.est_resale = (round(float(comps["est_resale"]) * mult, 2)
                        if comps["est_resale"] else None)
        e.price_low = (round(float(comps["price_low"]) * mult, 2)
                       if comps["price_low"] else None)
        e.price_high = (round(float(comps["price_high"]) * mult, 2)
                        if comps["price_high"] else None)
        e.comp_count = comps["comp_count"]
        e.price_source = comps["price_source"]
        if e.est_resale is not None:
            price_log.record(lot.id, float(e.est_resale), method="reprice",
                             price_source=e.price_source,
                             comp_count=e.comp_count, query=search_title)
        e.comps = comps.get("comps") or None
        if mult != 1.0 and comps["price_source"]:
            e.price_source += f" ×{mult:g} condition"
        _apply_estimate_cap(lot, e)
    # A changed value voids its old audit — the check certified a number
    # that no longer exists.
    if str(prior_resale) != str(e.est_resale):
        e.gold_check = None
        e.gold_check_note = None
    _apply_roi(lot, e)
    db.commit()
    try:
        # The one model spend in reprice: fresh golds get their second
        # opinion (~half a cent each).
        _verify_gold(db, lot, e)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Gold check failed for lot %s: %s", plan["lot_db_id"], exc)
    return True


def run_reprice(lot_db_ids: list[int], resume_job_id: str | None = None) -> None:
    """Recompute comps + ROI for already-enriched lots, reusing the AI title
    and verdict we already paid for.

    Lots are handled in batches of REPRICE_CONCURRENCY: plan them on the
    session, commit, fetch their comps concurrently with no session held,
    then apply the results in order. Only the network fans out; every
    database write stays on this thread. Cancellation is checked per batch.

    Pricing rules change (realization factor, condition multipliers, comp
    filters) far more often than an item's identity does — this applies them
    without spending anything on the model again.

    The full id list is persisted on the job row and `current` is the resume
    checkpoint: a deploy mid-run leaves the row behind, and startup calls back
    in with resume_job_id to continue where it stopped. Repricing is
    idempotent, so the one lot in flight at the kill just runs twice.
    """
    db: Session = SessionLocal()
    if resume_job_id:
        job = resume_job_id
        row = jobs.get(job)
        start_at = (row or {}).get("current") or 0
    else:
        job = jobs.start("reprice", "Re-pricing lots with current comp rules",
                         total=len(lot_db_ids), payload={"lot_ids": lot_db_ids})
        start_at = 0
    repriced = skipped = 0
    width = max(1, REPRICE_CONCURRENCY)
    try:
        i = start_at
        while i < len(lot_db_ids):
            if jobs.is_cancelled(job):
                print(f"Reprice cancelled after {i} lots")
                break
            batch = lot_db_ids[i:i + width]

            # --- A. plan on the session; no network yet
            plans = []
            for lot_db_id in batch:
                try:
                    plans.append(_plan_reprice_lot(db, lot_db_id))
                except Exception as exc:  # noqa: BLE001 — one bad lot must not stop the run
                    logger.warning("Reprice failed for lot %s: %s", lot_db_id, exc)
                    db.rollback()
                    plans.append({"lot_db_id": lot_db_id, "title": None, "kind": "failed"})
            # Hand the connection back before going out to the network.
            db.commit()

            # --- B. fetch comps concurrently, with no session held
            results = {}
            network = [p for p in plans if p["kind"] == "network"]
            if network:
                with ThreadPoolExecutor(max_workers=min(width, len(network))) as pool:
                    futures = {pool.submit(_fetch_reprice_comps, p): p["lot_db_id"]
                               for p in network}
                    for fut, lot_db_id in futures.items():
                        try:
                            results[lot_db_id] = fut.result()
                        except Exception as exc:  # noqa: BLE001
                            logger.warning("Reprice lookup failed for lot %s: %s",
                                           lot_db_id, exc)
                            results[lot_db_id] = None

            # --- C. apply in order, one checkpoint per lot
            for plan in plans:
                i += 1
                lot_db_id = plan["lot_db_id"]
                if plan["kind"] == "override":
                    skipped += 1
                elif plan["kind"] in ("title", "network"):
                    comps = (plan.get("comps") if plan["kind"] == "title"
                             else results.get(lot_db_id))
                    try:
                        if _apply_reprice(db, plan, comps):
                            repriced += 1
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("Reprice failed for lot %s: %s", lot_db_id, exc)
                        db.rollback()
                # `current` must advance on every lot — skipped, missing and
                # failed ones included — because it's the resume checkpoint,
                # not just the progress bar.
                jobs.update(job, current=i,
                            detail=(plan.get("title") or "")[:40] or None)
    finally:
        jobs.finish(job)
        db.close()
    print(f"Reprice complete: {repriced} updated, {skipped} kept (hand-corrected)")


def run_ship_analysis(auction_ids: list[int], resume_job_id: str | None = None) -> None:
    """AI-read each auction's shipping info + terms and store a rough
    per-item shipping cost estimate and a one-line policy summary.

    Takes auction ids, not pre-fetched texts: the worker pulls the
    shipping/terms blobs from HiBid itself (this is a sync thread with no
    running event loop, so asyncio.run is safe). That keeps the enqueueing
    request fast, keeps the job's persisted payload small, and lets a resumed
    run re-fetch texts for just the auctions it hasn't done yet. The id list
    rides the job row; `current` is the resume checkpoint. Auctions with no
    text at all get a summary without an AI call.
    """
    db: Session = SessionLocal()
    if resume_job_id:
        job = resume_job_id
        row = jobs.get(job)
        start_at = (row or {}).get("current") or 0
    else:
        job = jobs.start("ship-analysis", "Reading shipping terms per auction",
                         total=len(auction_ids),
                         payload={"auction_ids": auction_ids})
        start_at = 0
    analyzed = no_info = 0
    try:
        remaining = auction_ids[start_at:]
        auctions = {a.id: a for a in
                    db.query(models.Auction)
                      .filter(models.Auction.id.in_(remaining)).all()} if remaining else {}
        hibid_ids = [a.hibid_id for a in auctions.values() if a.hibid_id]
        meta: dict[int, dict] = {}
        if hibid_ids:
            jobs.update(job, label="Fetching shipping terms from HiBid…")

            async def _fetch_meta():
                async with httpx.AsyncClient() as http:
                    return await hibid.fetch_auction_meta(http, hibid_ids)

            meta = asyncio.run(_fetch_meta())
            jobs.update(job, label="Reading shipping terms per auction")

        for i, auction_id in enumerate(remaining, start_at + 1):
            if jobs.is_cancelled(job):
                print(f"Shipping analysis cancelled after {i - 1} auctions")
                break
            auction = auctions.get(auction_id)
            if not auction:
                jobs.update(job, current=i)
                continue
            m = meta.get(auction.hibid_id) or {}
            ship_text = (m.get("ship_text") or "").strip()
            terms_text = (m.get("terms_text") or "").strip()
            try:
                if not ship_text and not terms_text:
                    auction.ship_summary = "No shipping details posted"
                    auction.ship_cost_estimate = None
                    no_info += 1
                else:
                    result = _call_with_retry(lambda: client.messages.create(
                        model=MODEL,
                        max_tokens=300,
                        messages=[{"role": "user",
                                   "content": SHIPPING_PROMPT.format(
                                       ship_text=ship_text[:4000] or "(none posted)",
                                       terms_text=terms_text[:6000] or "(none posted)")}],
                    ).content[0].text)
                    if result is None:
                        raise RuntimeError("shipping AI call failed")
                    cost = result.get("cost_estimate")
                    auction.ship_cost_estimate = (round(float(cost), 2)
                                                  if isinstance(cost, (int, float)) else None)
                    auction.ship_summary = (result.get("summary") or "")[:500] or None
                    analyzed += 1
                auction.ship_analyzed_at = datetime.now(timezone.utc).replace(tzinfo=None)
                db.commit()
            except Exception as exc:  # noqa: BLE001 — one bad auction must not stop the run
                logger.warning("Shipping analysis failed for auction %s: %s",
                               auction_id, exc)
                db.rollback()
            # `current` advances on every auction — it's the resume checkpoint.
            jobs.update(job, current=i, detail=(auction.name or "")[:40])
    finally:
        jobs.finish(job)
        db.close()
    print(f"Shipping analysis complete: {analyzed} read, {no_info} had no info posted")
