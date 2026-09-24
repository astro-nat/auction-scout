"""POST /events, GET /events/summary - what the user actually does in the UI.

Kept in the app's own Postgres and never sent anywhere else. The question
this answers is "which features get used, and which never do", so the UI
can be shaped around the real workflow instead of guesses: if the ROI
filter is touched forty times a week and Watch never, that is a design
brief.

The client batches a few seconds of activity per request, so a click never
waits on the network; the server caps what it will take from one call, so
a runaway client cannot fill the table.
"""

import json
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db

router = APIRouter(prefix="/events", tags=["events"])

MAX_BATCH = 200          # events accepted per call; the rest are dropped
NAME_MAX = 60
PROPS_MAX_CHARS = 2000   # a props blob past this is replaced with a marker


@router.post("", status_code=202)
def record(payload: schemas.UiEventBatch, db: Session = Depends(get_db)):
    rows = []
    for ev in payload.events[:MAX_BATCH]:
        name = (ev.name or "").strip()[:NAME_MAX]
        if not name:
            continue
        props = ev.props if isinstance(ev.props, dict) else None
        if props is not None and len(json.dumps(props)) > PROPS_MAX_CHARS:
            props = {"_truncated": True}
        rows.append(models.UiEvent(name=name, view=(ev.view or None)[:40] if ev.view else None,
                                   props=props))
    if rows:
        db.add_all(rows)
        db.commit()
    return {"recorded": len(rows), "dropped": max(0, len(payload.events) - MAX_BATCH)}


@router.get("/summary")
def summary(days: int = 7, limit: int = 100, db: Session = Depends(get_db)):
    """Counts by feature over the window: totals per event name, and the
    most frequent (name, props, view) combinations - which button, which
    filter value, on which screen."""
    since = datetime.now(timezone.utc) - timedelta(days=days)
    base = db.query(models.UiEvent).filter(models.UiEvent.created_at >= since)
    total = base.count()
    by_name = dict(db.query(models.UiEvent.name, func.count())
                     .filter(models.UiEvent.created_at >= since)
                     .group_by(models.UiEvent.name).all())
    top = (db.query(models.UiEvent.name, models.UiEvent.props, models.UiEvent.view,
                    func.count().label("n"))
             .filter(models.UiEvent.created_at >= since)
             .group_by(models.UiEvent.name, models.UiEvent.props, models.UiEvent.view)
             .order_by(func.count().desc())
             .limit(min(limit, 500)).all())
    bounds = (db.query(func.min(models.UiEvent.created_at), func.max(models.UiEvent.created_at))
                .filter(models.UiEvent.created_at >= since).one())
    return {
        "days": days, "total": total, "by_name": by_name,
        "first_seen": bounds[0].isoformat() if bounds[0] else None,
        "last_seen": bounds[1].isoformat() if bounds[1] else None,
        "top": [{"name": n, "props": p, "view": v, "count": c} for n, p, v, c in top],
    }


@router.get("/recent")
def recent(limit: int = 50, db: Session = Depends(get_db)):
    rows = (db.query(models.UiEvent).order_by(models.UiEvent.id.desc())
              .limit(min(limit, 500)).all())
    return [{"name": r.name, "props": r.props, "view": r.view,
             "at": r.created_at.isoformat() if r.created_at else None} for r in rows]
