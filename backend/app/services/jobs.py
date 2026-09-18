"""Registry of what the server is doing right now — backed by the `jobs` table.

Long operations (scan, import, reprice, shipping analysis) register a job and
update it as they go; the frontend polls GET /status and renders a top bar.

This used to be an in-memory dict, which meant a Railway deploy or container
restart silently killed any batch mid-run with nothing left to resume from
(a frontend-only deploy once cut a 1,199-auction shipping analysis off at 167).
Rows in Postgres survive the process: `payload` carries the job's remaining
plan and `current` is its checkpoint, so workers/resume.py can restart the
resumable kinds ('reprice', 'ship-analysis') at startup. Request-scoped kinds
('scan', 'import') die with the HTTP request driving them; their leftover rows
are just cleared at startup.

Every helper opens its own short-lived session — callers hold their own
sessions/transactions mid-batch and this must never entangle with them. That
also keeps it safe across threads: sync background tasks run in a threadpool
while async routes update from the event loop.
"""

import logging
import os
import socket
import uuid
from typing import Optional

from sqlalchemy import func, or_, text

from .. import models
from ..database import SessionLocal

logger = logging.getLogger(__name__)

# Long, network-bound jobs. Only one of these runs at a time: they all hold a
# transaction open across their slow HTTP calls, so running three together
# just means three of them crawling while the connection pool starves every
# request behind them.
HEAVY_KINDS = ("reprice", "ship-analysis", "bid-refresh", "import", "scan")


# --- ownership -----------------------------------------------------------
# Identifies this process in `claimed_by`. Once the workers move out of the
# web process (phase 1) several processes will see the same rows, and the
# only safe way to decide who runs what is to make claiming atomic.
WORKER_ID = f"{socket.gethostname()}:{os.getpid()}"

# How long a job may go without a heartbeat before it's presumed dead.
# Generous on purpose: run_reprice heartbeats once per lot, and one lot can
# legitimately sit through several 40s comp lookups. Too low and the reaper
# starts fighting live jobs.
STALE_JOB_SECONDS = int(os.environ.get("STALE_JOB_SECONDS", "900"))

# Kinds that can be picked up again from their checkpoint. Everything else
# (scan, import) runs inside a request handler; a dead one is just litter.
RESUMABLE_KINDS = {"reprice": "lot_ids",
                   "ship-analysis": "auction_ids",
                   "bid-refresh": "auction_ids"}


def _stale_cutoff():
    """Staleness measured on the DATABASE clock, not this process's.

    Workers on different hosts have different clocks; the row is shared, so
    the comparison has to happen where the row lives.
    """
    return func.now() - text(f"interval '{int(STALE_JOB_SECONDS)} seconds'")


def heartbeat(job_id: str) -> None:
    """Say the owner is still alive. Folded into update() so every worker
    that reports progress heartbeats for free."""
    db = None
    try:
        db = SessionLocal()
        (db.query(models.Job)
           .filter(models.Job.id == job_id)
           .update({"heartbeat_at": func.now()}, synchronize_session=False))
        db.commit()
    except Exception as exc:  # noqa: BLE001 — same rule as update()
        logger.warning("Heartbeat for %s skipped: %s", job_id, exc)
    finally:
        if db is not None:
            db.close()


def claim(job_id: str) -> bool:
    """Take ownership of an abandoned job. True only if WE got it.

    One conditional UPDATE, so two processes racing for the same row can't
    both win — the second matches zero rows because the first already moved
    the heartbeat forward. (Phase 1's "claim the next pending job" query is
    the one that needs SELECT … FOR UPDATE SKIP LOCKED; targeting a known id
    doesn't.)
    """
    db = None
    try:
        db = SessionLocal()
        updated = (db.query(models.Job)
                     .filter(models.Job.id == job_id,
                             or_(models.Job.heartbeat_at.is_(None),
                                 models.Job.heartbeat_at < _stale_cutoff()))
                     .update({"claimed_by": WORKER_ID,
                              "heartbeat_at": func.now(),
                              "state": "running"},
                             synchronize_session=False))
        db.commit()
        return bool(updated)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Claim of %s failed: %s", job_id, exc)
        return False
    finally:
        if db is not None:
            db.close()


def stale() -> list[dict]:
    """Jobs whose owner has stopped saying anything."""
    db = None
    try:
        db = SessionLocal()
        rows = (db.query(models.Job)
                  .filter(or_(models.Job.heartbeat_at.is_(None),
                              models.Job.heartbeat_at < _stale_cutoff()))
                  .all())
        return [_as_dict(j, with_payload=True) for j in rows]
    except Exception as exc:  # noqa: BLE001
        logger.warning("Stale-job scan failed: %s", exc)
        return []
    finally:
        if db is not None:
            db.close()


def heavy_running(ignore: Optional[str] = None) -> Optional[str]:
    """The kind of the heavy job currently running, if any.

    `ignore` lets a caller ask "is anything OTHER than me running".
    """
    for job in active():
        kind = job.get("kind")
        if kind in HEAVY_KINDS and kind != ignore and not job.get("cancelled"):
            return kind
    return None


def start(kind: str, label: str, total: Optional[int] = None,
          payload: Optional[dict] = None) -> str:
    """Register a job and return its id. `kind` is a machine tag ('import',
    'scan', 'reprice', 'ship-analysis'); `label` is what the user reads.
    `payload` is whatever a resumable job needs to pick up after a restart."""
    job_id = uuid.uuid4().hex[:12]
    db = SessionLocal()
    try:
        db.add(models.Job(id=job_id, kind=kind, label=label,
                          total=total, payload=payload,
                          state="running", claimed_by=WORKER_ID,
                          heartbeat_at=func.now()))
        db.commit()
    finally:
        db.close()
    return job_id


def update(job_id: str, current: Optional[int] = None,
           total: Optional[int] = None, detail: Optional[str] = None,
           label: Optional[str] = None) -> None:
    values = {}
    if current is not None:
        values["current"] = current
    if total is not None:
        values["total"] = total
    if detail is not None:
        values["detail"] = detail
    if label is not None:
        values["label"] = label
    # No early return on empty values: update(job_id) with nothing to report
    # is still a caller saying "I'm alive", and the heartbeat below is the
    # whole point of letting it through.
    # Progress is cosmetic plus a resume checkpoint — never worth killing the
    # job over. This call sits outside the per-item try/except in run_reprice,
    # so a pool timeout here used to unwind the whole loop and strand a row
    # that showed progress forever without advancing.
    db = None
    try:
        db = SessionLocal()
        # Progress IS the heartbeat: anything still reporting is alive, and
        # this costs nothing extra — same row, same statement.
        values["heartbeat_at"] = func.now()
        (db.query(models.Job)
           .filter(models.Job.id == job_id)
           .update(values, synchronize_session=False))
        db.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Job %s progress update skipped: %s", job_id, exc)
    finally:
        if db is not None:
            db.close()


def finish(job_id: str) -> None:
    """Clear the row. Retried once: this runs in a finally block, and a row
    that outlives its worker is exactly the ghost job the status bar shows
    forever."""
    for attempt in (1, 2):
        db = None
        try:
            db = SessionLocal()
            (db.query(models.Job)
               .filter(models.Job.id == job_id)
               .delete(synchronize_session=False))
            db.commit()
            return
        except Exception as exc:  # noqa: BLE001
            logger.warning("Job %s cleanup failed (attempt %d): %s",
                           job_id, attempt, exc)
        finally:
            if db is not None:
                db.close()


def get(job_id: str) -> Optional[dict]:
    """Full row (payload included) — resume uses this to find its checkpoint."""
    db = SessionLocal()
    try:
        job = db.query(models.Job).filter(models.Job.id == job_id).first()
        return _as_dict(job, with_payload=True) if job else None
    finally:
        db.close()


def active() -> list[dict]:
    db = None
    try:
        db = SessionLocal()
        rows = db.query(models.Job).order_by(models.Job.started_at).all()
        return [_as_dict(j) for j in rows]
    except Exception as exc:  # noqa: BLE001
        # /status is how the user sees trouble — it has to answer even when
        # the pool is what's in trouble.
        logger.warning("Job listing unavailable: %s", exc)
        return []
    finally:
        if db is not None:
            db.close()


def cancel(job_id: str) -> bool:
    """Ask a job to stop. Workers check is_cancelled() at safe points — the
    work already done is kept, nothing is rolled back.

    Cancelling an ALREADY-cancelled job force-dismisses the row instead:
    if the worker died between acknowledging the cancel and finishing
    (e.g. the browser closed mid-request, so CancelledError skipped the
    handler's cleanup), the row would sit in the status bar until the next
    restart — a second Cancel click clears it on the spot."""
    db = SessionLocal()
    try:
        job = db.query(models.Job).filter(models.Job.id == job_id).first()
        if not job:
            return False
        if job.cancelled:
            db.delete(job)
            db.commit()
            return True
        job.cancelled = True
        job.label = f"Stopping — {job.label}"
        db.commit()
        return True
    finally:
        db.close()


def is_cancelled(job_id: str) -> bool:
    """A MISSING row also reads as cancelled: force-dismissing a stuck job
    deletes its row, and any thread still alive behind it must stop too —
    otherwise deletion would leave an unstoppable headless worker."""
    db = None
    try:
        db = SessionLocal()
        job = db.query(models.Job).filter(models.Job.id == job_id).first()
        return job is None or bool(job.cancelled)
    except Exception as exc:  # noqa: BLE001
        # Couldn't ask. A missing row means cancelled, but a failed LOOKUP
        # means nothing — treating it as a cancel would quietly abandon a
        # long job every time the pool got busy.
        logger.warning("Cancel check for %s failed, continuing: %s", job_id, exc)
        return False
    finally:
        if db is not None:
            db.close()


def has_active(kind: str) -> bool:
    """Is a non-cancelled job of this kind running? Endpoints use this to
    refuse stacking a second reprice/bid-refresh on top of a live one."""
    return any(j.get("kind") == kind and not j.get("cancelled") for j in active())


def _as_dict(job: "models.Job", with_payload: bool = False) -> dict:
    # Same shape the old in-memory registry returned — /status serves this
    # verbatim and the frontend reads id/label/current/total/cancelled.
    out = {
        "id": job.id, "kind": job.kind, "label": job.label,
        "current": job.current or 0, "total": job.total, "detail": job.detail,
        "cancelled": job.cancelled,
    }
    if with_payload:
        out["payload"] = job.payload
    return out
