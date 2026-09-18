"""Startup cleanup for work that died with a request.

Most recovery now happens elsewhere: batch jobs carry a heartbeat and the
reaper (workers/maintenance.py) restarts them from their checkpoint without
needing a restart at all, and queued lots are claimed straight from the
database by the worker process. What's left is the one case neither can
judge — `scan` and `import` run inside a request handler, so a row of
either kind belongs to a process that is definitively gone by the time a
new one boots. Waiting out the stale threshold for those would just leave
the status bar lying for fifteen minutes.
"""

import logging

from .. import models
from ..database import SessionLocal
from ..services import jobs

logger = logging.getLogger(__name__)


def clear_request_scoped_jobs() -> int:
    """Delete job rows whose HTTP request died with the previous process."""
    db = SessionLocal()
    try:
        removed = (db.query(models.Job)
                     .filter(models.Job.kind.notin_(list(jobs.RESUMABLE_KINDS)))
                     .delete(synchronize_session=False))
        db.commit()
        if removed:
            logger.info("Cleared %d request-scoped job row(s) from a dead process",
                        removed)
        return removed
    finally:
        db.close()
