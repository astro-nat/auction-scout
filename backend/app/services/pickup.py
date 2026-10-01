"""Pickup days and hours for the auctions you would drive to.

HiBid keeps each sale's pickup instructions ("Pick up by appointment on
FRIDAY or SATURDAY, OCT 2 or 3 ONLY") in a field of its own. Reading it is
free - no AI, no comps - but it is only worth having for sales close enough
to pick up from, so only those are asked: within MAX_DRIVE_MINUTES of the
drive-from address, still open, and not asked in the last REFRESH_AFTER.
"""

import asyncio
import logging
import threading
from datetime import datetime, timedelta, timezone

import httpx

from . import hibid, open_state

logger = logging.getLogger(__name__)

MAX_DRIVE_MINUTES = 45
# Houses edit pickup times as a sale goes on ("pickup moved to Saturday").
REFRESH_AFTER = timedelta(hours=12)
_lock = threading.Lock()


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
        for a in rows:
            info = got.get(a.hibid_id)
            if info is None:
                continue
            a.pickup_info = info["pickup_info"] or None
            a.address = a.address or info["address"]
            a.zip = a.zip or info["zip"]
            a.pickup_checked_at = now
        db.commit()
        logger.info("Pickup info: asked about %d auctions", len(rows))
        return len(rows)
    finally:
        _lock.release()
