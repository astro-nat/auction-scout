"""Pickup days and hours for the auctions you would drive to.

HiBid keeps each sale's pickup instructions ("Pick up by appointment on
FRIDAY or SATURDAY, OCT 2 or 3 ONLY") in a field of its own. Reading it is
free - no AI, no comps - but it is only worth having for sales close enough
to pick up from, so only those are asked: within MAX_DRIVE_MINUTES of the
drive-from address, still open, and not asked in the last REFRESH_AFTER.
"""

import asyncio
import logging
import re
import threading
from datetime import datetime, timedelta, timezone

import httpx

from . import hibid, open_state

logger = logging.getLogger(__name__)

MAX_DRIVE_MINUTES = 45
# Houses edit pickup times as a sale goes on ("pickup moved to Saturday").
REFRESH_AFTER = timedelta(hours=12)
_lock = threading.Lock()

# "Shipping only.", "No local pickup", "We do not offer pickup", "Ship only".
_SHIP_ONLY_RE = re.compile(
    r"\b(shipping only|ship only|ships only|no (local )?pick[ -]?ups?|"
    r"(do not|don't|does not) (offer|allow|have) (local )?pick[ -]?up)\b",
    re.IGNORECASE)


def is_ship_only(text) -> bool:
    return bool(text and _SHIP_ONLY_RE.search(text))


def will_pick_up(auction) -> bool:
    """You'd collect this sale's lots yourself: it is within driving reach
    and its pickup text doesn't rule pickup out. A lot tagged "Ship" there
    was being charged freight you would never pay."""
    if auction is None or getattr(auction, "ship_only", None):
        return False
    minutes = getattr(auction, "drive_minutes", None)
    return minutes is not None and minutes < MAX_DRIVE_MINUTES


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def due(db, now=None):
    """Auctions to ask about: close enough to drive to, still open, not
    asked recently."""
    from .. import models
    now = now or _utcnow()
    from sqlalchemy import or_
    return (db.query(models.Auction)
              .filter(models.Auction.hibid_id.isnot(None),
                      models.Auction.drive_minutes.isnot(None),
                      models.Auction.drive_minutes < MAX_DRIVE_MINUTES,
                      open_state.still_open(now),
                      or_(models.Auction.pickup_checked_at.is_(None),
                          models.Auction.pickup_checked_at < now - REFRESH_AFTER))
              .all())


def refresh(db) -> int:
    """Fill in pickup instructions for the auctions due. Returns how many
    were asked. One run at a time; a second caller returns at once."""
    if not _lock.acquire(blocking=False):
        return 0
    try:
        rows = due(db)
        if not rows:
            return 0

        async def _fetch():
            async with httpx.AsyncClient() as client:
                return await hibid.fetch_pickup(client, [a.hibid_id for a in rows])

        got = asyncio.run(_fetch())
        now = _utcnow()
        from ..workers.enrich import _apply_roi
        for a in rows:
            info = got.get(a.hibid_id)
            if info is None:
                continue
            a.pickup_info = info["pickup_info"] or None
            a.ship_only = is_ship_only(a.pickup_info)
            a.address = a.address or info["address"]
            a.zip = a.zip or info["zip"]
            a.pickup_checked_at = now
            # Whether you'd pick up decides the freight in each lot's cost;
            # re-grade them now (arithmetic only - no AI, no comps).
            for lot in a.lots:
                if lot.enrichment is not None and lot.enrichment.est_resale:
                    _apply_roi(lot, lot.enrichment)
        db.commit()
        logger.info("Pickup info: asked about %d auctions", len(rows))
        return len(rows)
    finally:
        _lock.release()
