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
from ..services import financials, pricing
from ..services.bolo import BoloMatcher

logger = logging.getLogger(__name__)

client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env
MODEL = "claude-haiku-4-5"      # cheap + fast; per-lot cost matters at 800 lots/auction

bolo_matcher = BoloMatcher()    # hot-reloads its JSON files on mtime change

MAX_ATTEMPTS = 2
MIN_DESC_FOR_TEXT_PASS = 80     # below this the description carries no signal

# Shipping-effort penalty ($) by logistics tier — an input to the ROI math,
# not a real shipping quote.
LOGISTICS_PENALTY = {"EASY": 15.0, "NEUTRAL": 25.0, "HARD": 60.0}

TEXT_PROMPT = """You are enriching a resale-auction lot for a reseller.
From the title and description, produce:
- enriched_title: the most specific eBay-searchable title (brand, model, era, product type; under 80 chars; no fluff or condition words)
- verdict: exactly one of "broken, damaged, or for parts" | "untested or unknown condition" | "mint condition or working perfectly" | "normal wear and tear"
- confident: true only if brand AND specific product type are identifiable
- notes: one sentence of resale-relevant context

Title: {title}
Description: {description}

Return ONLY valid JSON: {{"enriched_title": string, "verdict": string, "confident": boolean, "notes": string}}
"""

VISION_PROMPT = """Identify this auction lot from its photo for an eBay search.
Produce the most specific searchable title you can (brand, model, material, era, product type; under 80 chars).
A vintage/unbranded item is still confident if you can name 3+ visual specifics (material + color/pattern + form + era).
Mixed/bundled lots are never confident.

Original listing title: {title}

Return ONLY valid JSON: {{"enriched_title": string, "verdict": string, "confident": boolean, "notes": string}}
verdict must be exactly one of "broken, damaged, or for parts" | "untested or unknown condition" | "mint condition or working perfectly" | "normal wear and tear"
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
        e.last_attempted_at = datetime.now(timezone.utc)

        try:
            _enrich(lot, e)
            e.status = "success"
            e.error_message = None
        except Exception as exc:  # noqa: BLE001 — one lot failing must not kill a batch
            logger.warning("Enrichment failed for lot %s: %s", lot_db_id, exc)
            e.status = "failed"
            e.error_message = str(exc)
        db.commit()
    finally:
        db.close()


def _enrich(lot: models.Lot, e: models.Enrichment) -> None:
    title = lot.title or ""
    description = lot.description or ""
    # Fields the user hand-corrected are never overwritten by re-enrichment.
    protected = set(e.user_overrides or [])

    # --- 1. BOLO match (free, deterministic) ---
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
        ai = _call_text(title, description)
        e.ai_source = "text"
    if ai is None or not ai.get("confident"):
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
    elif e.ai_source is None:
        e.ai_source = "none"

    # --- 3. Comps (skipped entirely when the user hand-set the resale) ---
    if "est_resale" not in protected:
        search_title = e.enriched_title or title
        comps = pricing.lookup_comps(search_title)
        e.est_resale = comps["est_resale"]
        e.price_low = comps["price_low"]
        e.price_high = comps["price_high"]
        e.comp_count = comps["comp_count"]
        e.price_source = comps["price_source"]

    # --- 4. ROI ---
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
    elif red_flag or lot.unreachable_pickup:
        e.roi_status = "PASS"


INSPECT_PROMPT = """This is a photo of a multi-item auction lot titled: {title}

Identify each INDIVIDUALLY SELLABLE item you can actually read or recognize in
the photo — CD/DVD/book spines, game boxes, branded products, etc. For each,
give an eBay-searchable title (under 60 chars). Skip anything you can't
specifically identify — never guess or pad the list. Max 12 items.

Return ONLY valid JSON:
{{"items": [{{"title": string}}], "summary": string}}
summary = one sentence on what the lot contains overall.
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
            _inspect(lot, e)
            e.status = "success"
            e.error_message = None
        except Exception as exc:  # noqa: BLE001
            logger.warning("Inspection failed for lot %s: %s", lot_db_id, exc)
            e.status = "failed"
            e.error_message = str(exc)
        db.commit()
    finally:
        db.close()


def _inspect(lot: models.Lot, e: models.Enrichment) -> None:
    # Full-size image beats the thumbnails for reading spines/labels
    image_bytes = _download_image(lot.fullsize_url or lot.hd_thumbnail_url
                                  or lot.thumbnail_url)
    if not image_bytes:
        raise RuntimeError("no image available for inspection")

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
    for item in items:
        item_title = (item.get("title") or "").strip()
        if not item_title:
            continue
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
