"""Startup recovery for background work killed by a deploy or crash.

FastAPI BackgroundTasks run in-process, so a Railway deploy or container
restart kills them silently mid-run. Every long-running worker therefore
leaves its plan in the DB — a `jobs` row with a payload and checkpoint for
batch runs, per-lot 'queued' status on the enrichment table — and this
module, called once from the startup hook, turns whatever survived back
into running threads.

Threads rather than BackgroundTasks because there's no request to attach
to. Daemon threads on purpose: if the next deploy kills them too, the same
evidence is still in the DB and the startup after that resumes again.

Runs before the server accepts requests, so everything found here is a true
orphan — no risk of double-running work a live request just queued. (During
a rolling deploy the *old* container may still be finishing a lot or two
while we resume; both sides re-check DB state per item, so the overlap
costs at most one duplicated AI call, not corrupted data.)
"""

import threading

from .. import models
from ..database import SessionLocal
from .enrich import run_enrichment, run_inspection, run_reprice, run_ship_analysis


def resume_interrupted_work() -> None:
    db = SessionLocal()
    try:
        # --- batch jobs: resume the resumable kinds, clear the rest ---
        for row in db.query(models.Job).all():
            payload = row.payload or {}
            if row.kind == "reprice" and payload.get("lot_ids"):
                print(f"Resuming reprice {row.id} at {row.current}/{row.total}")
                _spawn(run_reprice, payload["lot_ids"], row.id)
            elif row.kind == "ship-analysis" and payload.get("auction_ids"):
                print(f"Resuming shipping analysis {row.id} at {row.current}/{row.total}")
                _spawn(run_ship_analysis, payload["auction_ids"], row.id)
            else:
                # scan/import run inside a request handler; that request died
                # with the old process, so the row is just stale.
                db.delete(row)
        db.commit()

        # --- enrichment queue: lots still 'queued' were waiting for a
        # BackgroundTask that no longer exists ---
        orphans = (db.query(models.Enrichment.lot_id, models.Enrichment.queued_task)
                     .filter(models.Enrichment.status == "queued")
                     .order_by(models.Enrichment.lot_id)
                     .all())
    finally:
        db.close()

    if orphans:
        print(f"Resuming {len(orphans)} queued enrichments orphaned by restart")
        threading.Thread(target=_run_enrichment_orphans,
                         args=([tuple(o) for o in orphans],),
                         daemon=True).start()


def _spawn(fn, id_list, job_id: str) -> None:
    threading.Thread(target=fn, args=(id_list,),
                     kwargs={"resume_job_id": job_id}, daemon=True).start()


def _run_enrichment_orphans(items: list[tuple[int, str | None]]) -> None:
    """Sequential, like a live batch (one request's BackgroundTasks run one
    at a time). Both workers re-check status == 'queued' per lot, so the
    Cancel button drains this loop the same way it drains a live batch."""
    for lot_id, task in items:
        if task == "inspect":
            run_inspection(lot_id)
        else:
            run_enrichment(lot_id)
