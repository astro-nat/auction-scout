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
    """Models sometimes wrap JSON in a ```json ... ``` fence despite being told not
    to. Strip it before parsing rather than relying on prompt compliance."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        text = text.removesuffix("```").strip()
    return json.loads(text)


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

    # --- 1. BOLO match (free, deterministic) ---
    match = bolo_matcher.match(title, description)
    if match:
        e.bolo_brand = match["brand"]
        e.bolo_category = match["category"]
        e.bolo_tier = str(match["tier"]) if match["tier"] is not None else None
        e.bolo_confidence = match["confidence"]
        e.matched_model = match["matched_model"]
        e.target_buy_price = match["target_buy_high"]
        e.ship_class = match["ship_class"]

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
        e.enriched_title = (ai.get("enriched_title") or "")[:80] or None
        e.verdict = ai.get("verdict")
        e.confidence = "strong" if ai.get("confident") else "weak"
        e.notes = ai.get("notes")
    elif e.ai_source is None:
        e.ai_source = "none"

    # --- 3. Comps ---
    search_title = e.enriched_title or title
    comps = pricing.lookup_comps(search_title)
    e.est_resale = comps["est_resale"]
    e.price_low = comps["price_low"]
    e.price_high = comps["price_high"]
    e.comp_count = comps["comp_count"]
    e.price_source = comps["price_source"]

    # --- 4. ROI ---
    red_flag = e.verdict in ("broken, damaged, or for parts",
                             "untested or unknown condition")
    if comps["est_resale"] and not lot.unreachable_pickup and not red_flag:
        penalty = LOGISTICS_PENALTY.get(lot.logistics_ease or "NEUTRAL", 25.0)
        effective_bid = float(max(lot.current_bid or 0, lot.next_bid or 0))
        lead = financials.evaluate_lead(
            resale_value=float(comps["est_resale"]),
            current_bid=effective_bid,
            logistics_penalty=penalty,
            dts=0.0,  # no sell-through data yet — don't fail lots on it
        )
        e.max_bid = lead.max_bid
        e.est_roi = lead.roi
        e.profit = lead.profit
        e.roi_status = lead.status
    elif red_flag or lot.unreachable_pickup:
        e.roi_status = "PASS"


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
