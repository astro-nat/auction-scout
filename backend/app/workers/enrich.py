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

For now this runs via FastAPI BackgroundTasks (see routers/enrichment.py). When you
outgrow that (need real concurrency limits, retry visibility, or to survive a backend
restart mid-job), swap the call site for an Arq/Celery task — this function's body
barely has to change.
"""

import json
import base64
import logging
from datetime import datetime, timezone

import anthropic
import httpx
from sqlalchemy.orm import Session

from ..database import SessionLocal
from .. import models, config
from ..services import financials, jobs, pricing
from ..services.bolo import BoloMatcher
from ..services.hibid import classify_logistics

logger = logging.getLogger(__name__)

client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env
MODEL = "claude-haiku-4-5"      # cheap + fast; per-lot cost matters at 800 lots/auction

bolo_matcher = BoloMatcher()    # hot-reloads its JSON files on mtime change

MAX_ATTEMPTS = 2
MIN_DESC_FOR_TEXT_PASS = 80     # below this the description carries no signal

# Shipping-effort penalty ($) by logistics tier — an input to the ROI math,
# not a real shipping quote.
LOGISTICS_PENALTY = {"EASY": 15.0, "NEUTRAL": 25.0, "HARD": 60.0}

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
- enriched_title: the most specific eBay-searchable title (brand, model, era, product type; under 80 chars; no fluff or condition words)
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
A vintage/unbranded item is still confident if you can name 3+ visual specifics (material + color/pattern + form + era).
Mixed/bundled lots are never confident.

Original listing title: {title}

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
        e.last_attempted_at = datetime.now(timezone.utc)

        try:
            _enrich(lot, e, db)
            e.status = "success"
            e.error_message = None
        except Exception as exc:  # noqa: BLE001 — one lot failing must not kill a batch
            logger.warning("Enrichment failed for lot %s: %s", lot_db_id, exc)
            e.status = "failed"
            e.error_message = str(exc)
        e.progress = None
        db.commit()
    finally:
        db.close()


def _enrich(lot: models.Lot, e: models.Enrichment, db: Session) -> None:
    title = lot.title or ""
    description = lot.description or ""
    # Fields the user hand-corrected are never overwritten by re-enrichment.
    protected = set(e.user_overrides or [])

    # --- 1. BOLO match (free, deterministic) ---
    _progress(db, e, "matching against BOLO brand list…")
    match = bolo_matcher.match(title, description)
    if match and "bolo_brand" not in protected:
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
            }
        )

    # --- 2. AI title + condition verdict ---
    ai = None
    if len(description.strip()) >= MIN_DESC_FOR_TEXT_PASS:
        _progress(db, e, "AI reading the description…")
        ai = _call_text(title, description)
        e.ai_source = "text"
    if ai is None or not ai.get("confident"):
        _progress(db, e, "AI examining the photo…")
        image_bytes = _download_image(lot.thumbnail_url or lot.hd_thumbnail_url)
        if image_bytes:
            vision = _call_vision(title, image_bytes)
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
        _progress(db, e, "searching eBay for comparable sales…")
        search_title = e.enriched_title or title
        comps = pricing.lookup_comps(search_title)
        mult = CONDITION_MULTIPLIER.get(e.verdict, 1.0)
        e.est_resale = (round(float(comps["est_resale"]) * mult, 2)
                        if comps["est_resale"] else None)
        e.price_low = (round(float(comps["price_low"]) * mult, 2)
                       if comps["price_low"] else None)
        e.price_high = (round(float(comps["price_high"]) * mult, 2)
                        if comps["price_high"] else None)
        e.comp_count = comps["comp_count"]
        e.price_source = comps["price_source"]
        if mult != 1.0 and comps["price_source"]:
            e.price_source += f" ×{mult:g} condition"

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
        penalty = LOGISTICS_PENALTY.get(lot.logistics_ease or "NEUTRAL", 25.0)
        effective_bid = float(max(lot.current_bid or 0, lot.next_bid or 0))
        lead = financials.evaluate_lead(
            resale_value=float(e.est_resale),
            current_bid=effective_bid,
            logistics_penalty=penalty,
            dts=0.0,  # no sell-through data yet — don't fail lots on it
        )
        e.max_bid = lead.max_bid
        e.est_roi = lead.roi
        e.profit = lead.profit
        e.roi_status = "PASS" if (red_flag or lot.unreachable_pickup) else lead.status
    else:
        # No usable price means no usable verdict. Leaving the previous run's
        # numbers in place is how a lot whose comps were just rejected stayed
        # the app's top "gold mine" at $3252 profit.
        e.max_bid = None
        e.profit = None
        e.est_roi = None
        e.roi_status = "PASS" if (red_flag or lot.unreachable_pickup) else None


INSPECT_PROMPT = """This is a photo of a multi-item auction lot titled: {title}

Identify each INDIVIDUALLY SELLABLE item you can actually read or recognize in
the photo — CD/DVD/book spines, game boxes, branded products, etc. For each,
give an eBay-searchable title (under 60 chars). Skip anything you can't
specifically identify — never guess or pad the list. Max 12 items.

Return ONLY valid JSON:
{{"items": [{{"title": string}}], "summary": string, "ship": string}}
summary = one sentence on what the lot contains overall.
ship = how hard the WHOLE lot is to ship, exactly one of "EASY" (fits a padded mailer or small box) | "NEUTRAL" (normal parcel or two) | "HARD" (furniture, appliance, oversized/freight)
"""

MAX_INSPECT_ITEMS = 12


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
    b64 = base64.b64encode(image_bytes).decode()
    result = _call_with_retry(lambda: client.messages.create(
        model=MODEL,
        max_tokens=800,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64",
                                             "media_type": "image/jpeg",
                                             "data": b64}},
                {"type": "text", "text": INSPECT_PROMPT.format(title=lot.title or "")},
            ],
        }],
    ))
    if result is None:
        raise RuntimeError("vision call failed")

    items = (result.get("items") or [])[:MAX_INSPECT_ITEMS]
    lines = []
    total = 0.0
    priced = 0
    for i, item in enumerate(items, 1):
        item_title = (item.get("title") or "").strip()
        if not item_title:
            continue
        _progress(db, e, f"pricing item {i}/{len(items)}: {item_title[:40]}…")
        comps = pricing.lookup_comps(item_title)
        if comps["est_resale"]:
            total += float(comps["est_resale"])
            priced += 1
            lines.append(f"{item_title} → ${comps['est_resale']} ({comps['comp_count']} comps)")
        else:
            lines.append(f"{item_title} → no comps")

    protected = set(e.user_overrides or [])
    summary = result.get("summary") or ""
    if "notes" not in protected:
        e.notes = f"[inspected: {len(items)} items, {priced} priced] {summary}\n" + "\n".join(lines)
    e.ai_source = "vision-itemized"
    # Vision saw the whole lot — trust its ship-tier call over the title regex.
    ship = (result.get("ship") or "").upper()
    if ship in ("EASY", "NEUTRAL", "HARD") and "logistics_ease" not in protected:
        lot.logistics_ease = ship
        _mark_ai_ship(e)
    if total > 0 and "est_resale" not in protected:
        e.est_resale = round(total, 2)
        e.price_low = None
        e.price_high = None
        e.comp_count = priced
        e.price_source = f"itemized vision ({priced}/{len(items)} items priced)"
        _apply_roi(lot, e)


# ------------------------------------------------------------------ AI calls

def _call_with_retry(make_call) -> dict | None:
    last: Exception | None = None
    for _ in range(MAX_ATTEMPTS):
        try:
            resp = make_call()
            return _parse_json_response(resp.content[0].text)
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
    ))


def _call_vision(title: str, image_bytes: bytes) -> dict | None:
    b64 = base64.b64encode(image_bytes).decode()
    return _call_with_retry(lambda: client.messages.create(
        model=MODEL,
        max_tokens=400,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64",
                                             "media_type": "image/jpeg",
                                             "data": b64}},
                {"type": "text", "text": VISION_PROMPT.format(title=title)},
            ],
        }],
    ))


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


def run_reprice(lot_db_ids: list[int]) -> None:
    """Recompute comps + ROI for already-enriched lots, reusing the AI title
    and verdict we already paid for.

    Pricing rules change (realization factor, condition multipliers, comp
    filters) far more often than an item's identity does — this applies them
    without spending anything on the model again.
    """
    db: Session = SessionLocal()
    job = jobs.start("reprice", "Re-pricing lots with current comp rules",
                     total=len(lot_db_ids))
    repriced = skipped = 0
    try:
        for i, lot_db_id in enumerate(lot_db_ids, 1):
            if jobs.is_cancelled(job):
                print(f"Reprice cancelled after {i - 1} lots")
                break
            lot = db.query(models.Lot).filter(models.Lot.id == lot_db_id).first()
            if not lot or not lot.enrichment:
                continue
            e = lot.enrichment
            if "est_resale" in set(e.user_overrides or []):
                skipped += 1            # never overwrite a hand-corrected price
                continue
            try:
                # Reclassify ship tier with the current regex rules — free,
                # and ship-rule fixes should reach old lots the same way
                # pricing-rule fixes do. Hand-set tiers ("logistics_ease")
                # and AI-set tiers ("logistics_ease_ai" sentinel) survive.
                marks = set(e.user_overrides or [])
                if not marks & {"logistics_ease", "logistics_ease_ai"}:
                    lot.logistics_ease = classify_logistics(
                        lot.title or "", lot.category or "", lot.description or "")
                comps = pricing.lookup_comps(e.enriched_title or lot.title)
                mult = CONDITION_MULTIPLIER.get(e.verdict, 1.0)
                e.est_resale = (round(float(comps["est_resale"]) * mult, 2)
                                if comps["est_resale"] else None)
                e.price_low = (round(float(comps["price_low"]) * mult, 2)
                               if comps["price_low"] else None)
                e.price_high = (round(float(comps["price_high"]) * mult, 2)
                                if comps["price_high"] else None)
                e.comp_count = comps["comp_count"]
                e.price_source = comps["price_source"]
                if mult != 1.0 and comps["price_source"]:
                    e.price_source += f" ×{mult:g} condition"
                _apply_roi(lot, e)
                db.commit()
                repriced += 1
            except Exception as exc:  # noqa: BLE001 — one bad lot must not stop the run
                logger.warning("Reprice failed for lot %s: %s", lot_db_id, exc)
            jobs.update(job, current=i, detail=(lot.title or "")[:40])
    finally:
        jobs.finish(job)
        db.close()
    print(f"Reprice complete: {repriced} updated, {skipped} kept (hand-corrected)")


def run_ship_analysis(items: list[dict]) -> None:
    """AI-read each auction's shipping info + terms and store a rough
    per-item shipping cost estimate and a one-line policy summary.

    ``items`` is [{"auction_id", "name", "ship_text", "terms_text"}, ...] —
    the texts are fetched by the (async) route handler since this worker is
    sync. Auctions with no text at all get a summary without an AI call.
    """
    db: Session = SessionLocal()
    job = jobs.start("ship-analysis", "Reading shipping terms per auction",
                     total=len(items))
    analyzed = no_info = 0
    try:
        for i, item in enumerate(items, 1):
            if jobs.is_cancelled(job):
                print(f"Shipping analysis cancelled after {i - 1} auctions")
                break
            auction = (db.query(models.Auction)
                         .filter(models.Auction.id == item["auction_id"]).first())
            if not auction:
                continue
            ship_text = (item.get("ship_text") or "").strip()
            terms_text = (item.get("terms_text") or "").strip()
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
                    ))
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
                               item["auction_id"], exc)
                db.rollback()
            jobs.update(job, current=i, detail=(item.get("name") or "")[:40])
    finally:
        jobs.finish(job)
        db.close()
    print(f"Shipping analysis complete: {analyzed} read, {no_info} had no info posted")
