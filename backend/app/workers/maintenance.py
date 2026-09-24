"""Periodic housekeeping — the closed-item flush and the stale-job reaper.

Daemon threads on fixed cadences: un-biddable lots get deleted every
FLUSH_CLOSED_HOURS (0 disables). Bids are never refreshed automatically -
only the Refresh bids button moves them - and there is deliberately no
setting that turns an automatic refresh back on.
"""

import logging
import os
import threading
import time
from datetime import datetime

from ..database import SessionLocal
from .. import config, models

logger = logging.getLogger(__name__)

STARTUP_DELAY_SECONDS = 60


def start_maintenance() -> None:
    _start_flush_loop()
    _start_reaper()


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


REAPER_INTERVAL_SECONDS = int(os.environ.get("REAPER_INTERVAL_SECONDS", "120"))


def _start_reaper() -> None:
    """Pick up after workers that died without cleaning up.

    A job row outliving its worker is the failure that cost us most: the
    status bar shows progress that never advances, the kind is blocked
    against restarting, and the only cure was a redeploy. Twice in one day.

    A lapsed heartbeat is what distinguishes a dead job from a slow one.
    Resumable kinds get restarted from their checkpoint; the rest ran inside
    a request handler that is long gone, so their rows are just litter.
    """
    # Imported here, not at module scope: workers.enrich builds the Anthropic
    # client at import time, and housekeeping shouldn't depend on that.
    from ..services import jobs
    from .enrich import run_reprice, run_ship_analysis
    from .import_all import run_import_all
    from .refresh import run_bid_refresh

    def loop():
        runners = {"reprice": run_reprice,
                   "ship-analysis": run_ship_analysis,
                   "bid-refresh": run_bid_refresh,
                   "import-all": run_import_all}
        time.sleep(STARTUP_DELAY_SECONDS)
        while True:
            try:
                for row in jobs.stale():
                    kind = row.get("kind")
                    if row.get("cancelled"):
                        # Asked to stop, then its worker died before it
                        # could. Restarting it only to have it read its own
                        # cancel flag and stop again is a pointless round
                        # trip — and it briefly re-occupies the one heavy
                        # slot on the way past.
                        logger.info("Reaping cancelled %s job %s", kind, row["id"])
                        jobs.finish(row["id"])
                        continue
                    payload_key = jobs.RESUMABLE_KINDS.get(kind)
                    ids = (row.get("payload") or {}).get(payload_key or "")
                    if not (payload_key and ids):
                        logger.warning("Reaping dead %s job %s (not resumable)",
                                       kind, row["id"])
                        jobs.finish(row["id"])
                        continue
                    # Only one process may revive it — claim() is the guard,
                    # and losing the race means somebody else got there.
                    if not jobs.claim(row["id"]):
                        continue
                    logger.warning("Restarting dead %s job %s at %s/%s",
                                   kind, row["id"], row.get("current"), row.get("total"))
                    threading.Thread(target=runners[kind], args=(ids,),
                                     kwargs={"resume_job_id": row["id"]},
                                     daemon=True).start()
                jobs.prune_workers()
            except Exception as exc:  # noqa: BLE001 — the reaper must outlive its own bugs
                logger.warning("Reaper pass failed: %s", exc)
            time.sleep(REAPER_INTERVAL_SECONDS)

    threading.Thread(target=loop, daemon=True, name="job-reaper").start()
    logger.info("Job reaper on: every %ss, stale after %ss",
                REAPER_INTERVAL_SECONDS, jobs.STALE_JOB_SECONDS)
