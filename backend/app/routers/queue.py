"""GET /queue - everything started and not yet finished, and what is left.

/status answers "is anything running" in one line for the top bar. This
answers "what is left": every background job with how far it has got and
the next few things it will touch, and every lot waiting to be priced in
the order the worker will take them.

Read-only, and cheap enough to poll every few seconds while the Queue view
is open: one query for the jobs, one name lookup per job for its next few
items, one for the waiting lots.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from .. import models
from ..database import get_db
from ..services.jobs import RESUMABLE_KINDS

router = APIRouter(tags=["queue"])

# How many upcoming items to name per job, and how many waiting lots to list.
NEXT_UP = 20
LOTS_MAX = 500


def _names(db: Session, key: str, ids: list[int]) -> list[str]:
    """Display names for a job's upcoming ids, in the job's own order."""
    if not ids:
        return []
    if key == "lot_ids":
        rows = db.query(models.Lot.id, models.Lot.title).filter(models.Lot.id.in_(ids)).all()
    else:
        rows = (db.query(models.Auction.id, models.Auction.name)
                  .filter(models.Auction.id.in_(ids)).all())
    by_id = {i: n for i, n in rows}
    return [by_id[i] or f"#{i}" for i in ids if i in by_id]


@router.get("/queue")
def get_queue(db: Session = Depends(get_db)):
    jobs = (db.query(models.Job)
              .order_by((models.Job.state == "pending"), models.Job.started_at)
              .all())
    out_jobs = []
    for j in jobs:
        current = j.current or 0
        item = {
            "id": j.id, "kind": j.kind, "label": j.label,
            "state": j.state or "running", "current": current, "total": j.total,
            "remaining": (max(0, j.total - current) if j.total is not None else None),
            "detail": j.detail, "cancelled": bool(j.cancelled),
            "started_at": j.started_at.isoformat() if j.started_at else None,
            "next_up": [], "next_up_more": 0,
        }
        key = RESUMABLE_KINDS.get(j.kind)
        ids = (j.payload or {}).get(key) if key else None
        if isinstance(ids, list):
            left = ids[current:]
            item["next_up"] = _names(db, key, left[:NEXT_UP])
            item["next_up_more"] = max(0, len(left) - NEXT_UP)
        out_jobs.append(item)

    waiting_q = (db.query(models.Enrichment, models.Lot, models.Auction.name)
                   .join(models.Lot, models.Lot.id == models.Enrichment.lot_id)
                   .outerjoin(models.Auction, models.Auction.id == models.Lot.auction_id)
                   .filter(models.Enrichment.status == "queued"))
    total = waiting_q.count()
    # The worker's own claim order (services.jobs.claim_lots), so position 1
    # here is the next lot it takes.
    rows = (waiting_q.order_by(models.Enrichment.queued_at.nullsfirst(),
                               models.Enrichment.queue_rank.nullsfirst(),
                               models.Enrichment.lot_id)
                     .limit(LOTS_MAX).all())
    lots = [{
        "lot_db_id": lot.id, "lot_id": lot.lot_id, "title": lot.title,
        "lot_link": lot.lot_link, "auction_id": lot.auction_id, "auction_name": auction_name,
        "task": e.queued_task or "enrich",
        # A lot being worked publishes a stage string; the rest are waiting.
        "stage": e.progress,
        "queued_at": e.queued_at.isoformat() if e.queued_at else None,
    } for e, lot, auction_name in rows]
    return {"jobs": out_jobs, "lots": {"total": total, "items": lots}}
