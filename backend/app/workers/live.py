"""Live Auction mode: while an auction is live, its bids refresh every
minute.

Bids otherwise move only when someone presses Refresh bids. During a sale
that is the wrong cadence - the bid that matters changes minute to minute,
and the one question is whether it has gone past what the lot is worth to
you. So a switched-on auction is read from HiBid every LIVE_INTERVAL
seconds (about ten catalogue pages for a 900-lot sale), re-graded at its
new bids, and switched off by itself once its last lot has closed.

An auction goes live by itself once any of its lots closes within the
hour (auto_start), if you have imported lots from it - a sale with nothing
of yours in it isn't worth the HiBid reads. Switching one off by hand opts
it out of that, so the rule doesn't switch it straight back on.

It used to push an ntfy alert when a watched lot's bid passed your max.
Dropped 2026-10-03 at the user's word - "i never remember what they are
and i don't really care". The Live view's own "N watched past your max"
line still shows it, for whoever is looking. Lot.max_passed_alert_at is
left in the schema, unused.
"""

import logging
import threading
import time
from datetime import datetime, timedelta, timezone


from ..database import SessionLocal
from .. import models
from ..services import open_state

logger = logging.getLogger(__name__)

LIVE_INTERVAL = 60
# An auction with a lot closing within this window goes live by itself.
AUTO_LIVE_WINDOW = timedelta(hours=1)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def auto_start(db, now) -> int:
    """Switch on every auction with an open lot closing within the hour -
    HiBid sales you have imported lots from, not opted out by hand.
    Returns how many went live."""
    from sqlalchemy import exists
    from sqlalchemy.orm import aliased
    lot = aliased(models.Lot)
    soon = exists().where(lot.auction_id == models.Auction.id,
                          lot.closes_at.isnot(None),
                          lot.closes_at >= now,
                          lot.closes_at <= now + AUTO_LIVE_WINDOW)
    rows = (db.query(models.Auction)
              .filter(models.Auction.hibid_id.isnot(None),
                      models.Auction.live.isnot(True),
                      models.Auction.live_opt_out.isnot(True),
                      soon)
              .all())
    for a in rows:
        a.live = True
        a.live_auto = True
        logger.info("Live mode on for %s: lots close within the hour", a.name)
    if rows:
        db.commit()
    return len(rows)


def run_once() -> int:
    """One pass over every live auction. Returns how many were refreshed."""
    from .refresh import run_bid_refresh
    db = SessionLocal()
    refreshed = 0
    try:
        now = _utcnow()
        auto_start(db, now)
        live = db.query(models.Auction).filter(models.Auction.live.is_(True)).all()
        for auction in live:
            over = (db.query(models.Auction)
                      .filter(models.Auction.id == auction.id, open_state.is_over(now))
                      .count())
            if over:
                auction.live = False
                db.commit()
                logger.info("Live mode off for %s: its last lot has closed", auction.name)
                continue
            # Quiet: a job row every minute would bury the real work in the
            # Queue tab. The refresh itself is the same one the button runs.
            run_bid_refresh([auction.id], track_job=False)
            db.expire_all()
            auction.live_refreshed_at = _utcnow()
            db.commit()
            refreshed += 1
    finally:
        db.close()
    return refreshed


def start_live_loop() -> None:
    def loop():
        while True:
            started = time.monotonic()
            try:
                run_once()
            except Exception as exc:  # noqa: BLE001 - the loop outlives its own bugs
                logger.warning("Live refresh failed: %s", exc)
            time.sleep(max(5, LIVE_INTERVAL - (time.monotonic() - started)))

    threading.Thread(target=loop, daemon=True, name="live-auctions").start()
    logger.info("Live Auction mode on: every %ds", LIVE_INTERVAL)
