"""Periodic housekeeping — the closed-item flush and the bid refresh.

Daemon threads on fixed cadences: bids (and per-lot closed statuses)
re-pull from HiBid every BID_REFRESH_HOURS; un-biddable lots get deleted
every FLUSH_CLOSED_HOURS. Each loop's first run fires shortly after boot
(every deploy restarts the clock, so in practice both happen at least on
their interval and often sooner). Setting either to 0 disables that loop.
"""

import logging
import threading
import time
from datetime import datetime

from ..database import SessionLocal
from .. import config, models

logger = logging.getLogger(__name__)

STARTUP_DELAY_SECONDS = 60


def start_maintenance() -> None:
    _start_flush_loop()
    _start_bid_refresh_loop()


def _start_flush_loop() -> None:
    if config.FLUSH_CLOSED_HOURS <= 0:
        logger.info("Auto-flush disabled — FLUSH_CLOSED_HOURS is 0")
        return

    def loop():
        # Imported here, not top-level: the router module pulls in half the
        # app, and worker imports shouldn't dictate module load order.
        from ..routers.lots import flush_closed_now
        time.sleep(STARTUP_DELAY_SECONDS)
        while True:
            try:
                db = SessionLocal()
                try:
                    r = flush_closed_now(db)
                finally:
                    db.close()
                if r["lots"] or r["auctions"]:
                    print(f"Auto-flush: removed {r['lots']} closed lots "
                          f"and {r['auctions']} empty auctions")
            except Exception as exc:  # noqa: BLE001 — housekeeping must never crash the app
                logger.warning("Auto-flush failed: %s", exc)
            time.sleep(config.FLUSH_CLOSED_HOURS * 3600)

    threading.Thread(target=loop, daemon=True, name="maintenance-flush").start()
    logger.info("Auto-flush on: every %.1fh", config.FLUSH_CLOSED_HOURS)


def _start_bid_refresh_loop() -> None:
    if config.BID_REFRESH_HOURS <= 0:
        logger.info("Auto bid refresh disabled — BID_REFRESH_HOURS is 0")
        return

    def loop():
        from ..services import jobs
        from .refresh import run_bid_refresh
        # Offset from the flush loop's wakeup so they don't pile onto the
        # DB at the same instant after a deploy.
        time.sleep(STARTUP_DELAY_SECONDS * 2)
        while True:
            try:
                # A refresh may already be running (manual button, or the
                # previous tick on a slow HiBid day) — never stack two.
                if not any(j.get("kind") == "bid-refresh" for j in jobs.active()):
                    db = SessionLocal()
                    try:
                        imported = (db.query(models.Lot.auction_id)
                                      .filter(models.Lot.auction_id.isnot(None))
                                      .distinct())
                        ids = [r[0] for r in
                               db.query(models.Auction.id)
                                 .filter(models.Auction.id.in_(imported),
                                         models.Auction.hibid_id.isnot(None))
                                 .filter((models.Auction.closing_date.is_(None))
                                         | (models.Auction.closing_date >= datetime.now()))
                                 .all()]
                    finally:
                        db.close()
                    if ids:
                        run_bid_refresh(ids)   # blocks this loop until done — that's fine
            except Exception as exc:  # noqa: BLE001
                logger.warning("Auto bid refresh failed: %s", exc)
            time.sleep(config.BID_REFRESH_HOURS * 3600)

    threading.Thread(target=loop, daemon=True, name="maintenance-bids").start()
    logger.info("Auto bid refresh on: every %.1fh", config.BID_REFRESH_HOURS)
