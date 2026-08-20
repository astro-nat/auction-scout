"""
Enrichment worker.

This is the ONLY file in the backend that imports the Anthropic SDK. Keeping the
AI call isolated here — rather than in a route handler — is what makes it safe for
this call to be slow or occasionally fail without taking the API down with it.

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
from sqlalchemy.orm import Session

from ..database import SessionLocal
from .. import models

logger = logging.getLogger(__name__)

client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env

MAX_ATTEMPTS = 3

PROMPT = """You are grading a resale auction lot for a reseller's BOLO (be-on-lookout) system.
Given the lot title and description below, identify the brand (if any), item category,
a buy-tier, and a target buy price ceiling for a healthy resale margin.

Title: {title}
Description: {description}

Return ONLY valid JSON, no other text, matching exactly this shape:
{{"brand": string|null, "category": string, "tier": "1"|"2"|"3", "target_buy_price": number|null, "confidence": "strong"|"weak", "notes": string}}
"""


def run_enrichment(lot_db_id: int) -> None:
    """Entry point called from the background task. Opens its own DB session
    since it runs outside the request/response cycle's session lifetime."""
    db: Session = SessionLocal()
    try:
        lot = db.query(models.Lot).filter(models.Lot.id == lot_db_id).first()
        if not lot:
            logger.warning("Lot %s vanished before enrichment ran", lot_db_id)
            return

        enrichment = lot.enrichment
        enrichment.last_attempted_at = datetime.now(timezone.utc)

        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                result = _call_model(lot.title, lot.description or "")
                enrichment.status = "success"
                enrichment.bolo_brand = result.get("brand")
                enrichment.bolo_category = result.get("category")
                enrichment.bolo_tier = result.get("tier")
                enrichment.target_buy_price = result.get("target_buy_price")
                enrichment.confidence = result.get("confidence")
                enrichment.notes = result.get("notes")
                enrichment.error_message = None
                db.commit()
                return
            except Exception as exc:  # noqa: BLE001 — deliberately broad, we log and retry
                logger.warning("Enrichment attempt %s/%s failed for lot %s: %s",
                                attempt, MAX_ATTEMPTS, lot_db_id, exc)
                if attempt == MAX_ATTEMPTS:
                    enrichment.status = "failed"
                    enrichment.error_message = str(exc)
                    db.commit()
    finally:
        db.close()


def _call_model(title: str, description: str) -> dict:
    """Text-only pass. Add an image pass (base64 photo in the content list) only
    for lots this doesn't resolve confidently — it's the slower, pricier call."""
    resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=400,
        messages=[{
            "role": "user",
            "content": PROMPT.format(title=title, description=description),
        }],
    )
    return json.loads(resp.content[0].text)


def _call_model_with_image(title: str, description: str, image_bytes: bytes) -> dict:
    """Vision pass — same idea, with a photo attached. Call this from run_enrichment
    when the text-only pass comes back with confidence == 'weak'."""
    b64 = base64.b64encode(image_bytes).decode()
    resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=400,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}},
                {"type": "text", "text": PROMPT.format(title=title, description=description)},
            ],
        }],
    )
    return json.loads(resp.content[0].text)
