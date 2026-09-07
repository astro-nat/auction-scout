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

import uuid
from typing import Optional

from .. import models
from ..database import SessionLocal


def start(kind: str, label: str, total: Optional[int] = None,
          payload: Optional[dict] = None) -> str:
    """Register a job and return its id. `kind` is a machine tag ('import',
    'scan', 'reprice', 'ship-analysis'); `label` is what the user reads.
    `payload` is whatever a resumable job needs to pick up after a restart."""
    job_id = uuid.uuid4().hex[:12]
    db = SessionLocal()
    try:
        db.add(models.Job(id=job_id, kind=kind, label=label,
                          total=total, payload=payload))
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
    if not values:
        return
    db = SessionLocal()
    try:
        (db.query(models.Job)
           .filter(models.Job.id == job_id)
           .update(values, synchronize_session=False))
        db.commit()
    finally:
        db.close()


def finish(job_id: str) -> None:
    db = SessionLocal()
    try:
        (db.query(models.Job)
           .filter(models.Job.id == job_id)
           .delete(synchronize_session=False))
        db.commit()
    finally:
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
    db = SessionLocal()
    try:
        rows = db.query(models.Job).order_by(models.Job.started_at).all()
        return [_as_dict(j) for j in rows]
    finally:
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
    db = SessionLocal()
    try:
        job = db.query(models.Job).filter(models.Job.id == job_id).first()
        return job is None or bool(job.cancelled)
    finally:
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
