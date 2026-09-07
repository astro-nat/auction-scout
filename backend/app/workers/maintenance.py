"""Periodic housekeeping — currently just the closed-item flush.

A daemon thread deletes lots from closed auctions (and the then-empty
auctions) on a fixed cadence, so the database and the items view don't
silt up with un-biddable leftovers between manual flushes.

First run fires a minute after boot (every deploy restarts the clock, so
in practice cleanup happens at least every FLUSH_CLOSED_HOURS and often
sooner), then repeats on the interval. FLUSH_CLOSED_HOURS=0 disables.
"""

import logging
import threading
import time

from ..database import SessionLocal
from .. import config

logger = logging.getLogger(__name__)

STARTUP_DELAY_SECONDS = 60


def start_maintenance() -> None:
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
                    print(f"Auto-flush: removed {r['lots']} closed-auction lots "
                          f"and {r['auctions']} empty auctions")
            except Exception as exc:  # noqa: BLE001 — housekeeping must never crash the app
                logger.warning("Auto-flush failed: %s", exc)
            time.sleep(config.FLUSH_CLOSED_HOURS * 3600)

    threading.Thread(target=loop, daemon=True, name="maintenance").start()
    logger.info("Auto-flush on: every %.1fh", config.FLUSH_CLOSED_HOURS)
