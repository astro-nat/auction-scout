"""In-process registry of what the server is doing right now.

Long operations (scan, import) register a job and update it as they go; the
frontend polls GET /status and renders a top bar. Deliberately in-memory:
this is live progress, not history — a restart should forget it, and the
single-replica deployment means there's nobody else to share it with.

Thread-safe because FastAPI runs sync background tasks in a threadpool while
async routes update from the event loop.
"""

import threading
import uuid
from typing import Optional

_lock = threading.Lock()
_jobs: dict[str, dict] = {}


def start(kind: str, label: str, total: Optional[int] = None) -> str:
    """Register a job and return its id. `kind` is a machine tag ('import',
    'scan'); `label` is what the user reads."""
    job_id = uuid.uuid4().hex[:12]
    with _lock:
        _jobs[job_id] = {
            "id": job_id, "kind": kind, "label": label,
            "current": 0, "total": total, "detail": None,
            "cancelled": False,
        }
    return job_id


def update(job_id: str, current: Optional[int] = None,
           total: Optional[int] = None, detail: Optional[str] = None,
           label: Optional[str] = None) -> None:
    with _lock:
        job = _jobs.get(job_id)
        if not job:
            return
        if current is not None:
            job["current"] = current
        if total is not None:
            job["total"] = total
        if detail is not None:
            job["detail"] = detail
        if label is not None:
            job["label"] = label


def finish(job_id: str) -> None:
    with _lock:
        _jobs.pop(job_id, None)


def active() -> list[dict]:
    with _lock:
        return list(_jobs.values())


def cancel(job_id: str) -> bool:
    """Ask a job to stop. Workers check is_cancelled() at safe points — the
    work already done is kept, nothing is rolled back."""
    with _lock:
        job = _jobs.get(job_id)
        if not job:
            return False
        job["cancelled"] = True
        job["label"] = f"Stopping — {job['label']}"
        return True


def is_cancelled(job_id: str) -> bool:
    with _lock:
        job = _jobs.get(job_id)
        return bool(job and job["cancelled"])
