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
    from .workers.enrich import (run_audit_sweep, run_regrade, run_reprice,
                                 run_ship_analysis)
    from .workers.import_all import run_import_all
    from .workers.refresh import run_bid_refresh
    return {
        "reprice": run_reprice,
        "ship-analysis": run_ship_analysis,
        "bid-refresh": run_bid_refresh,
        "regrade": run_regrade,
        "audit-golds": run_audit_sweep,
        "import-all": run_import_all,
    }.get(kind)


def _start_batch_job() -> bool:
    """Claim and start one pending batch job. True if we started something."""
    # No "is anything running?" check here any more: claim_pending() folds
    # the one-heavy-job-at-a-time limit into its own query, so the database
    # decides in a single statement instead of two workers both passing a
    # check and then both claiming.
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


def _run_one_lot(item) -> None:
    """One claimed lot, on a pool thread. Imports are lazy for the same
    reason as _runner_for — the Anthropic client builds at import time."""
    from .workers.enrich import run_comps, run_enrichment, run_inspection
    lot_id, task = item
    try:
        if task == "inspect":
            run_inspection(lot_id)
        elif task == "comps":
            run_comps(lot_id)
        else:
            run_enrichment(lot_id)
    except Exception:  # noqa: BLE001 — one lot must not kill the pool
        logger.exception("Queued lot %s failed in pool", lot_id)


def _work_lots(pool, inflight: set) -> bool:
    """Keep the lot pool continuously fed.

    The old shape claimed ENRICH_CONCURRENCY lots and JOINED the whole batch
    before claiming more — one 40s comp lookup idled every other slot, so
    real throughput was well under concurrency × avg. Now each finished slot
    is refilled on the next poll tick while the slow one keeps running.
    """
    inflight -= {f for f in set(inflight) if f.done()}
    free = max(0, config.ENRICH_CONCURRENCY - len(inflight))
    if not free:
        return False
    items = jobs.claim_lots(free)
    if not items:
        return False
    logger.info("Working %d queued lots (%d already in flight)",
                len(items), len(inflight))
    for item in items:
        inflight.add(pool.submit(_run_one_lot, item))
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

    from concurrent.futures import ThreadPoolExecutor
    pool = ThreadPoolExecutor(max_workers=max(1, config.ENRICH_CONCURRENCY),
                              thread_name_prefix="lot")
    inflight: set = set()

    while not _stop.is_set():
        try:
            jobs.worker_heartbeat()
            did = _start_batch_job()
            did = _work_lots(pool, inflight) or did
        except Exception:  # noqa: BLE001 — the loop outlives its own bugs
            logger.exception("Worker pass failed")
            did = False
        if not did:
            _stop.wait(IDLE_SLEEP_SECONDS)

    logger.info("Worker stopping")


if __name__ == "__main__":
    main()
