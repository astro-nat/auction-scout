"""Phase timings for long jobs, stored in Postgres rather than logged.

Railway keeps a shallow log buffer and the CLI returns a few dozen lines at
a time, so "read the logs afterwards" does not work for a job that ran for
twenty minutes. These rows survive, and /timings reads them back.

The thing this exists to answer: a 3,000-lot import takes 6 seconds against
a Postgres in the same Docker network and much longer against Railway. The
suspect is per-lot round trips - the save loop does a SELECT per lot, so
network latency multiplies by the lot count - but that is a guess until
each phase is measured separately.

Overhead is one INSERT per phase, not per item. Counters accumulate in
memory and are written once when the phase closes.
"""

import logging
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Optional

from .. import models
from ..database import SessionLocal

logger = logging.getLogger(__name__)


class Phase:
    """Accumulator handed to the body of a `timed` block."""

    def __init__(self, kind: str, phase: str):
        self.kind = kind
        self.phase = phase
        self.items = 0
        self.detail: dict = {}
        self._sub: dict = {}

    def add(self, n: int = 1) -> None:
        """Count items processed, for a per-item figure."""
        self.items += n

    def note(self, **fields) -> None:
        """Attach anything else worth seeing next to the duration."""
        self.detail.update(fields)

    @contextmanager
    def sub(self, name: str):
        """Time an inner step that runs many times per phase.

        Accumulates total seconds and a call count, so a per-lot database
        lookup shows up as one row rather than three thousand.
        """
        t0 = time.perf_counter()
        try:
            yield
        finally:
            slot = self._sub.setdefault(name, [0.0, 0])
            slot[0] += time.perf_counter() - t0
            slot[1] += 1

    def _sub_summary(self) -> dict:
        return {
            name: {"total_ms": round(total * 1000, 1),
                   "calls": calls,
                   "avg_ms": round(total * 1000 / calls, 3) if calls else 0}
            for name, (total, calls) in self._sub.items()
        }


@contextmanager
def timed(kind: str, phase: str, *, job_id: Optional[str] = None,
          auction_id: Optional[int] = None, label: Optional[str] = None):
    """Record how long one phase of a job took.

    Never raises: a timing row failing to write must not take down the work
    it was measuring.
    """
    p = Phase(kind, phase)
    started = datetime.now(timezone.utc)
    t0 = time.perf_counter()
    failed = None
    try:
        yield p
    except BaseException as exc:  # noqa: BLE001 - record, then re-raise
        failed = type(exc).__name__
        raise
    finally:
        seconds = time.perf_counter() - t0
        detail = dict(p.detail)
        subs = p._sub_summary()
        if subs:
            detail["steps"] = subs
        if failed:
            detail["failed"] = failed
        db = None
        try:
            db = SessionLocal()
            db.add(models.TaskTiming(
                kind=kind, phase=phase, job_id=job_id, auction_id=auction_id,
                label=label, started_at=started,
                duration_ms=round(seconds * 1000, 1),
                items=p.items or None,
                per_item_ms=(round(seconds * 1000 / p.items, 3)
                             if p.items else None),
                detail=detail or None))
            db.commit()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Timing row for %s/%s skipped: %s", kind, phase, exc)
        finally:
            if db is not None:
                db.close()
        logger.info("[timing] %s/%s %.1fs%s", kind, phase, seconds,
                    f" over {p.items} items ({seconds * 1000 / p.items:.1f}ms each)"
                    if p.items else "")
