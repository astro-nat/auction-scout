"""Periodic housekeeping — the closed-item flush and the bid refresh.

Daemon threads on fixed cadences: bids (and per-lot closed statuses)
re-pull from HiBid every BID_REFRESH_HOURS; un-biddable lots get deleted
every FLUSH_CLOSED_HOURS. Each loop's first run fires shortly after boot
(every deploy restarts the clock, so in practice both happen at least on
their interval and often sooner). Setting either to 0 disables that loop.
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
    _start_bid_refresh_loop()
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


def _start_bid_refresh_loop() -> None:
    if config.BID_REFRESH_HOURS <= 0:
        logger.info("Auto bid refresh disabled — BID_REFRESH_HOURS is 0")
        return

    def loop():
        from ..services import jobs
        from .refresh import auctions_due_for_bid_refresh, run_bid_refresh
        # Offset from the flush loop's wakeup so they don't pile onto the
        # DB at the same instant after a deploy.
        time.sleep(STARTUP_DELAY_SECONDS * 2)
        while True:
            try:
                # Skip this tick if ANY long job holds the pool — the hourly
                # refresh is not worth crawling a reprice to a halt. The next
                # tick picks it up.
                # Enqueue rather than run: the worker loop serialises heavy
                # jobs, so this no longer has to decide whether to skip.
                if jobs.has_pending("bid-refresh"):
                    logger.info("Auto bid refresh skipped — one is already queued")
                else:
                    db = SessionLocal()
                    try:
                        ids = auctions_due_for_bid_refresh(db)
                    finally:
                        db.close()
                    if not ids:
                        logger.info(
                            "Auto bid refresh: nothing closing within %.1fh",
                            config.BID_REFRESH_WINDOW_HOURS)
                    if ids:
                        # Queue it like any other caller would. Running it
                        # inline here blocked this loop for the duration and
                        # dodged the worker's one-heavy-job-at-a-time limit.
                        jobs.enqueue("bid-refresh", "Refreshing current bids",
                                     total=len(ids), payload={"auction_ids": ids})
            except Exception as exc:  # noqa: BLE001
                logger.warning("Auto bid refresh failed: %s", exc)
            time.sleep(config.BID_REFRESH_HOURS * 3600)

    threading.Thread(target=loop, daemon=True, name="maintenance-bids").start()
    logger.info("Auto bid refresh on: every %.1fh", config.BID_REFRESH_HOURS)

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
