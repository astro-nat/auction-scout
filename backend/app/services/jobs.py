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
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import and_, func, or_, select, text

from .. import models
from ..database import SessionLocal

logger = logging.getLogger(__name__)

# Long, network-bound jobs. claim_pending() lets only one run at a time:
# each holds a transaction open across its slow HTTP calls, so running three
# together just means three of them crawling while the pool starves
# everything behind them. That limit lives inside the claim query rather
# than in a check beside it, so two workers can't both decide they're clear.
HEAVY_KINDS = ("reprice", "ship-analysis", "bid-refresh", "import", "scan",
               "import-all")


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
                   "bid-refresh": "auction_ids",
                   "import-all": "auction_ids"}


# How long a job of each kind may go quiet before it's presumed dead.
#
# One threshold for everything meant 15 minutes for all of them, pinned to
# the slowest: run_reprice heartbeats once per lot, and one lot can sit
# through several 40s comp lookups across four query variants. Applying that
# to a bid refresh — which heartbeats once per auction and takes seconds —
# left a visibly stuck job looking normal for a quarter of an hour.
STALE_BY_KIND = {
    "bid-refresh": float(os.environ.get("STALE_BID_REFRESH_SECONDS", "300")),
    "regrade": float(os.environ.get("STALE_REGRADE_SECONDS", "180")),
    "reprice": float(os.environ.get("STALE_REPRICE_SECONDS", "900")),
    "ship-analysis": float(os.environ.get("STALE_SHIP_ANALYSIS_SECONDS", "900")),
}


def _interval(seconds: float):
    """now() - interval, on the DATABASE clock.

    Deliberately not the caller's clock: workers run on other hosts with
    their own, and the row is shared.
    """
    return func.now() - text(f"interval '{int(seconds)} seconds'")


def _stale_cutoff():
    """Default cutoff, for tables without a per-kind notion (queued lots)."""
    return _interval(STALE_JOB_SECONDS)


def _kind_clauses(compare):
    """Build one clause per kind plus a fallback, joined by the caller.

    `compare(column, cutoff)` decides the direction — `<` for stale,
    `>=` for still-alive — so the two stay in step by construction.
    """
    clauses = [
        and_(models.Job.kind == kind,
             compare(models.Job.heartbeat_at, _interval(secs)))
        for kind, secs in STALE_BY_KIND.items()
    ]
    clauses.append(and_(models.Job.kind.notin_(list(STALE_BY_KIND)),
                        compare(models.Job.heartbeat_at,
                                _interval(STALE_JOB_SECONDS))))
    return clauses


def _is_stale():
    """Never reported in, or quiet longer than its kind allows."""
    return or_(models.Job.heartbeat_at.is_(None),
               *_kind_clauses(lambda col, cutoff: col < cutoff))


def _is_alive():
    """Reported in recently enough for its kind. Not simply NOT stale: a
    NULL heartbeat is neither, and must not count as alive."""
    return and_(models.Job.heartbeat_at.isnot(None),
                or_(*_kind_clauses(lambda col, cutoff: col >= cutoff)))


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
                     .filter(models.Job.id == job_id, _is_stale())
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
                  .filter(_is_stale())
                  .all())
        return [_as_dict(j, with_payload=True) for j in rows]
    except Exception as exc:  # noqa: BLE001
        logger.warning("Stale-job scan failed: %s", exc)
        return []
    finally:
        if db is not None:
            db.close()


def enqueue(kind: str, label: str, total: Optional[int] = None,
            payload: Optional[dict] = None) -> str:
    """Register work for the worker process to pick up.

    The difference from start(): this row is NOT claimed. start() is what a
    runner calls when it is already executing; enqueue() is what an API
    handler calls to ask for execution somewhere else.
    """
    job_id = uuid.uuid4().hex[:12]
    db = SessionLocal()
    try:
        db.add(models.Job(id=job_id, kind=kind, label=label,
                          total=total, payload=payload, state="pending"))
        db.commit()
    finally:
        db.close()
    return job_id


def claim_pending(kinds: Optional[list[str]] = None,
                  max_heavy: int = 1) -> Optional[dict]:
    """Take the oldest claimable pending job, or None.

    Two things happen in this one statement, on purpose.

    FOR UPDATE SKIP LOCKED is what makes several workers safe: each
    transaction locks the row it takes and the others step over it rather
    than blocking or picking the same one. Unlike claim(), which targets a
    known id, this SELECTS — so it genuinely needs it.

    The heavy-job limit is a filter in the same query rather than a check
    before it. Asking "is anything heavy running?" and then claiming is two
    statements with a gap in the middle, and two workers can both pass the
    check before either claims. Folded in here, the database decides once.
    """
    db = None
    try:
        db = SessionLocal()
        # Serialise the whole claim. SKIP LOCKED protects the PENDING rows
        # being selected, but the heavy-job count below reads RUNNING rows —
        # different rows, unprotected. Under READ COMMITTED each worker's
        # snapshot can predate the others' commits, so they all count zero
        # and all claim: four racing workers started three heavy jobs.
        #
        # An advisory lock is exactly the tool for a read-modify-write that
        # spans rows. It's held for this transaction only, and claiming takes
        # milliseconds, so the serialisation costs nothing real.
        db.execute(text("SELECT pg_advisory_xact_lock(:k)"),
                   {"k": _CLAIM_LOCK_KEY})
        # Live = running AND still reporting. A heavy job whose worker died
        # must not hold the slot shut until the reaper gets round to it.
        heavy_live = (select(func.count())
                      .select_from(models.Job)
                      .where(models.Job.state == "running",
                             models.Job.kind.in_(HEAVY_KINDS),
                             _is_alive())
                      .scalar_subquery())
        q = (db.query(models.Job)
               .filter(models.Job.state == "pending",
                       models.Job.cancelled.is_(False),
                       or_(models.Job.kind.notin_(HEAVY_KINDS),
                           heavy_live < max_heavy))
               .order_by(models.Job.started_at)
               .with_for_update(skip_locked=True)
               .limit(1))
        if kinds:
            q = q.filter(models.Job.kind.in_(kinds))
        job = q.first()
        if job is None:
            db.rollback()
            return None
        job.state = "running"
        job.claimed_by = WORKER_ID
        job.heartbeat_at = func.now()
        out = _as_dict(job, with_payload=True)
        db.commit()
        return out
    except Exception as exc:  # noqa: BLE001
        logger.warning("Claiming a pending job failed: %s", exc)
        return None
    finally:
        if db is not None:
            db.close()


def claim_lots(limit: int) -> list[tuple]:
    """Take up to `limit` queued lots, oldest batch first.

    Returns [(lot_db_id, task)] — the shape process_queued_lots wants.

    Ordered by (queued_at, queue_rank) so the rows the user could see when
    they pressed the button are still worked first. A claim older than
    STALE_JOB_SECONDS is treated as abandoned and can be taken again, which
    is how a lot survives its worker being killed mid-flight.
    """
    db = None
    try:
        db = SessionLocal()
        rows = (db.query(models.Enrichment)
                  .filter(models.Enrichment.status == "queued",
                          or_(models.Enrichment.claimed_at.is_(None),
                              models.Enrichment.claimed_at < _stale_cutoff()))
                  .order_by(models.Enrichment.queued_at.nullsfirst(),
                            models.Enrichment.queue_rank.nullsfirst(),
                            models.Enrichment.lot_id)
                  .with_for_update(skip_locked=True)
                  .limit(limit)
                  .all())
        if not rows:
            db.rollback()
            return []
        claimed = [(r.lot_id, r.queued_task or "enrich") for r in rows]
        for r in rows:
            r.claimed_at = datetime.now(timezone.utc)
        db.commit()
        return claimed
    except Exception as exc:  # noqa: BLE001
        logger.warning("Claiming queued lots failed: %s", exc)
        return []
    finally:
        if db is not None:
            db.close()


# A worker is presumed gone this long after its last poll. Much tighter than
# STALE_JOB_SECONDS: this says "the process is alive", not "the job is
# making progress", and the loop touches it every few seconds.
WORKER_TIMEOUT_SECONDS = int(os.environ.get("WORKER_TIMEOUT_SECONDS", "90"))

# Arbitrary constant — any two processes agreeing on it exclude each other.
_CLAIM_LOCK_KEY = 8274112233


def worker_heartbeat() -> None:
    """Record that this worker process is alive."""
    db = None
    try:
        db = SessionLocal()
        now = datetime.now(timezone.utc)
        row = (db.query(models.WorkerHeartbeat)
                 .filter(models.WorkerHeartbeat.id == WORKER_ID).first())
        if row:
            row.last_seen = now
        else:
            db.add(models.WorkerHeartbeat(id=WORKER_ID, last_seen=now))
        db.commit()
    except Exception as exc:  # noqa: BLE001 — bookkeeping never kills a worker
        logger.warning("Worker heartbeat skipped: %s", exc)
    finally:
        if db is not None:
            db.close()


def live_workers() -> list[dict]:
    """Workers that have checked in recently.

    Empty means nothing is consuming the queue — the state that became
    silent when the work moved out of the API. No request fails; jobs just
    sit at 'pending' looking like they're about to start.
    """
    db = None
    try:
        db = SessionLocal()
        cutoff = func.now() - text(f"interval '{int(WORKER_TIMEOUT_SECONDS)} seconds'")
        rows = (db.query(models.WorkerHeartbeat)
                  .filter(models.WorkerHeartbeat.last_seen >= cutoff)
                  .order_by(models.WorkerHeartbeat.started_at)
                  .all())
        return [{"id": r.id,
                 "last_seen": r.last_seen.isoformat() if r.last_seen else None}
                for r in rows]
    except Exception as exc:  # noqa: BLE001
        logger.warning("Worker listing failed: %s", exc)
        return []
    finally:
        if db is not None:
            db.close()


def prune_workers() -> None:
    """Drop rows for processes long gone, so the table doesn't collect one
    per container the platform has ever started."""
    db = None
    try:
        db = SessionLocal()
        (db.query(models.WorkerHeartbeat)
           .filter(models.WorkerHeartbeat.last_seen < func.now() - text("interval '1 day'"))
           .delete(synchronize_session=False))
        db.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Worker prune skipped: %s", exc)
    finally:
        if db is not None:
            db.close()


def has_pending(kind: str) -> bool:
    """Is a job of this kind already queued or running?

    Replaces heavy_running() in the API. Under a queue, refusing unrelated
    work because something else is busy is wrong — it would simply wait its
    turn. What's still worth refusing is a DUPLICATE: three reprices from
    three clicks do the same work three times.
    """
    db = None
    try:
        db = SessionLocal()
        return bool(db.query(
            db.query(models.Job)
              .filter(models.Job.kind == kind,
                      models.Job.cancelled.is_(False))
              .exists()).scalar())
    except Exception as exc:  # noqa: BLE001
        logger.warning("Pending check for %s failed: %s", kind, exc)
        return False
    finally:
        if db is not None:
            db.close()


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
