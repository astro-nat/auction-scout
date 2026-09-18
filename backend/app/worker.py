"""The background worker — everything long-running, out of the web process.

Run it with `python -m app.worker`, from the same image as the API.

Why it exists: enrichment, repricing, shipping analysis and bid refreshes
used to run as FastAPI BackgroundTasks, i.e. inside the API process, sharing
its connection pool. Three long jobs each hold a transaction open across
their HTTP calls, and once the pool was exhausted every request queued
behind them — the backend wedged twice in one day, and a deploy killed
whatever was mid-flight. Separating the processes fixes the whole class:
a starved or stuck worker can no longer take the API down with it, and
either side can be restarted without the other noticing.

Postgres is the queue. There is no Redis and no broker: the `jobs` table
already carries a payload and a checkpoint per batch job, and the
`enrichment` table already marks per-lot work 'queued'. Both are claimed
with SELECT … FOR UPDATE SKIP LOCKED, so running several workers is safe —
each takes rows the others have stepped over.

Two kinds of work, polled in the same loop:
  - batch jobs (reprice, ship-analysis, bid-refresh, regrade) — one at a
    time, on a thread, because they're long and compete for the same pool
  - per-lot enrichment — up to ENRICH_CONCURRENCY at once

Liveness is the jobs-table heartbeat from phase 0: a worker killed
mid-batch leaves a row whose heartbeat stops, and the reaper restarts it
from its checkpoint. Nothing here needs to shut down cleanly.
"""

import logging
import os
import signal
import threading
import time

from . import config
from .services import jobs

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [worker] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# How long to wait after finding nothing. Short enough that pressing
# "Enrich" feels immediate, long enough not to hammer the database.
IDLE_SLEEP_SECONDS = float(os.environ.get("WORKER_POLL_SECONDS", "3"))

_stop = threading.Event()


def _runner_for(kind: str):
    """Imported lazily — workers.enrich builds the Anthropic client at import
    time, and that shouldn't happen just because someone imported this."""
    from .workers.enrich import run_regrade, run_reprice, run_ship_analysis
    from .workers.refresh import run_bid_refresh
    return {
        "reprice": run_reprice,
        "ship-analysis": run_ship_analysis,
        "bid-refresh": run_bid_refresh,
        "regrade": run_regrade,
    }.get(kind)


def _start_batch_job() -> bool:
    """Claim and start one pending batch job. True if we started something."""
    if jobs.heavy_running():
        return False          # one long job at a time — they share a pool
    job = jobs.claim_pending()
    if not job:
        return False
    runner = _runner_for(job["kind"])
    if runner is None:
        logger.warning("No runner for job kind %r — discarding %s",
                       job["kind"], job["id"])
        jobs.finish(job["id"])
        return False
    payload = job.get("payload") or {}
    key = jobs.RESUMABLE_KINDS.get(job["kind"])
    args = (payload.get(key),) if key else ()
    if key and not args[0]:
        logger.warning("Job %s (%s) has no payload — discarding",
                       job["id"], job["kind"])
        jobs.finish(job["id"])
        return False

    def run():
        try:
            runner(*args, resume_job_id=job["id"])
        except Exception:  # noqa: BLE001 — one job must not kill the worker
            logger.exception("Job %s (%s) failed", job["id"], job["kind"])
            jobs.finish(job["id"])

    logger.info("Starting %s job %s", job["kind"], job["id"])
    threading.Thread(target=run, daemon=True,
                     name=f"job-{job['kind']}").start()
    return True


def _work_lots() -> bool:
    """Claim and process a slice of the per-lot queue."""
    from .workers.enrich import process_queued_lots
    items = jobs.claim_lots(max(1, config.ENRICH_CONCURRENCY))
    if not items:
        return False
    logger.info("Working %d queued lots", len(items))
    process_queued_lots(items)
    return True


def main() -> None:
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: _stop.set())

    logger.info("Worker %s starting (poll %.1fs, %d lots at a time)",
                jobs.WORKER_ID, IDLE_SLEEP_SECONDS, config.ENRICH_CONCURRENCY)

    # Housekeeping lives here, not in the API: the reaper, the closed-item
    # flush, the hourly bid refresh and the closing-soon notifier. Running
    # them in the web process meant every extra API replica would duplicate
    # them, and they competed with requests for the same connections.
    from .workers.maintenance import start_maintenance
    from .workers.notify import start_notifier
    start_maintenance()
    start_notifier()

    while not _stop.is_set():
        try:
            did = _start_batch_job()
            did = _work_lots() or did
        except Exception:  # noqa: BLE001 — the loop outlives its own bugs
            logger.exception("Worker pass failed")
            did = False
        if not did:
            _stop.wait(IDLE_SLEEP_SECONDS)

    logger.info("Worker stopping")


if __name__ == "__main__":
    main()
