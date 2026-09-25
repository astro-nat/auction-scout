"""GET /queue - everything started and not yet finished, and what is left.

/status answers "is anything running" in one line for the top bar. This
answers "what is left": every background job with how far it has got and
the next few things it will touch, and every lot waiting to be priced in
the order the worker will take them.

Cheap enough to poll every few seconds while the Queue view is open: one
query for the jobs, one name lookup per job for its next few items, one for
the waiting lots. The move endpoints below change the order the worker takes
waiting jobs and lots in.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session

from .. import models
from ..database import get_db
from ..services.jobs import RESUMABLE_KINDS, _stale_cutoff

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
              .order_by((models.Job.state == "pending"),
                        models.Job.queue_pos.asc().nullslast(),
                        models.Job.started_at)
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
    # What the worker holds right now reads first: a reordered waiting list
    # can sort ahead of a lot already in flight, which the worker isn't
    # going to put down.
    lots.sort(key=lambda l: l["stage"] is None)
    return {"jobs": out_jobs, "lots": {"total": total, "items": lots}}


# --- reordering ---------------------------------------------------------

MOVES = ("top", "up", "down", "bottom")


def moved(ids: list, target, to: str) -> list:
    """`ids` with `target` moved to the top, one up, one down or to the
    bottom. Unchanged when the target isn't there or can't move that way."""
    if target not in ids or to not in MOVES:
        return list(ids)
    out = [i for i in ids if i != target]
    at = ids.index(target)
    where = {"top": 0, "bottom": len(out),
             "up": max(0, at - 1), "down": min(len(out), at + 1)}[to]
    out.insert(where, target)
    return out


def _check_move(to: str) -> None:
    if to not in MOVES:
        raise HTTPException(status_code=422, detail=f"to must be one of {', '.join(MOVES)}")


@router.post("/queue/jobs/{job_id}/move")
def move_job(job_id: str, to: str, db: Session = Depends(get_db)):
    """Move a job that hasn't started yet. A running job has no place in
    line to change."""
    _check_move(to)
    waiting = (db.query(models.Job)
                 .filter(models.Job.state == "pending")
                 .order_by(models.Job.queue_pos.asc().nullslast(), models.Job.started_at)
                 .all())
    ids = [j.id for j in waiting]
    if job_id not in ids:
        raise HTTPException(status_code=409, detail="Only a job that hasn't started can be moved")
    order = moved(ids, job_id, to)
    by_id = {j.id: j for j in waiting}
    for pos, i in enumerate(order):
        by_id[i].queue_pos = float(pos)
    db.commit()
    return {"order": order}


@router.post("/queue/lots/{lot_db_id}/move")
def move_lot(lot_db_id: int, to: str, db: Session = Depends(get_db)):
    """Move a lot waiting to be priced. The waiting list is renumbered in
    its new order - one queued_at for the lot, rank = position - which is
    the order the worker takes lots in (services.jobs.claim_lots). A lot
    the worker already holds is being worked and stays out of it."""
    _check_move(to)
    waiting = (db.query(models.Enrichment)
                 .filter(models.Enrichment.status == "queued",
                         or_(models.Enrichment.claimed_at.is_(None),
                             models.Enrichment.claimed_at < _stale_cutoff()))
                 .order_by(models.Enrichment.queued_at.nullsfirst(),
                           models.Enrichment.queue_rank.nullsfirst(),
                           models.Enrichment.lot_id)
                 .all())
    ids = [e.lot_id for e in waiting]
    if lot_db_id not in ids:
        raise HTTPException(status_code=409,
                            detail="Only a lot that is still waiting can be moved")
    order = moved(ids, lot_db_id, to)
    by_lot = {e.lot_id: e for e in waiting}
    # Keep the list where it was relative to lots queued later: the earliest
    # time already in it, so a batch queued afterwards still comes after.
    stamps = [e.queued_at for e in waiting if e.queued_at is not None]
    base = min(stamps) if stamps else datetime.now(timezone.utc)
    for rank, i in enumerate(order):
        by_lot[i].queued_at = base
        by_lot[i].queue_rank = rank
    db.commit()
    return {"position": order.index(lot_db_id) + 1, "waiting": len(order)}
