from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, joinedload
from typing import Optional, List

from .. import models, schemas
from ..database import get_db

router = APIRouter(prefix="/lots", tags=["lots"])

# How long a won lot (and its enrichment) survives the closed-items flush
# after being marked. A week covers photographing and listing the item off
# the stored identification and comps; after that it ages out normally.
WON_RETENTION_DAYS = 7


@router.get("/count")
def count_lots(
    category: Optional[str] = None,
    status: Optional[str] = None,
    auction_id: Optional[List[int]] = Query(None, description="repeatable — any of these auctions"),
    roi_status: Optional[str] = None,
    bolo_only: bool = False,
    include_closed: bool = False,
    db: Session = Depends(get_db),
):
    """How many lots match these filters — so the UI can say 'showing 2000 of
    10,559' instead of implying the page size is the whole database."""
    q = db.query(models.Lot)
    if category:
        q = q.filter(models.Lot.category == category)
    if auction_id:
        q = q.filter(models.Lot.auction_id.in_(auction_id))
    if status or roi_status or bolo_only:
        q = q.join(models.Enrichment)
    if status:
        q = q.filter(models.Enrichment.status == status)
    if roi_status:
        q = q.filter(models.Enrichment.roi_status == roi_status)
    if bolo_only:
        q = q.filter(models.Enrichment.bolo_brand.isnot(None))
    return {"total": q.count()}


@router.get("/categories")
def lot_categories(db: Session = Depends(get_db)):
    """Distinct categories across every imported lot, with how many lots each
    holds and how many of those are still enrichable (pending/failed, in an
    auction that hasn't closed). Powers the items view's category filter and
    its 'Enrich category' button. Declared before /{lot_id} so 'categories'
    can't be swallowed as a lot id."""
    from datetime import datetime
    from sqlalchemy import and_, case, func, or_
    enrichable = case(
        (and_(models.Enrichment.status.in_(["pending", "failed"]),
              or_(models.Auction.closing_date.is_(None),
                  models.Auction.closing_date >= datetime.now())), 1),
        else_=0)
    rows = (db.query(models.Lot.category,
                     func.count(models.Lot.id),
                     func.sum(enrichable))
              .join(models.Enrichment)
              .outerjoin(models.Auction, models.Lot.auction_id == models.Auction.id)
              .filter(models.Lot.category.isnot(None))
              .group_by(models.Lot.category)
              .order_by(models.Lot.category)
              .all())
    return [{"category": c, "lots": n, "enrichable": int(e or 0)}
            for c, n, e in rows]


@router.get("", response_model=List[schemas.LotOut])
def list_lots(
    category: Optional[str] = None,
    status: Optional[str] = Query(None, description="pending | queued | success | failed"),
    auction_id: Optional[List[int]] = Query(None, description="repeatable — any of these auctions"),
    roi_status: Optional[str] = Query(None, description="GOLD MINE | PASS"),
    bolo_only: bool = False,
    include_closed: bool = False,
    limit: int = 2000,
    offset: int = 0,
    db: Session = Depends(get_db),
):
    # Hard ceiling regardless of what the client asks for — a 6000-row
    # response (each lot with enrichment + itemized notes) crashed mobile
    # browser tabs and helped OOM the backend once.
    limit = min(limit, 2000)
    # Imported lots are kept visible even after their auction closes — the
    # enrichment work is yours, and a vanished lot looks like data loss. The
    # row is marked closed instead (see auction_closed below).
    q = (db.query(models.Lot)
           .options(joinedload(models.Lot.enrichment),
                    joinedload(models.Lot.auction)))
    if category:
        q = q.filter(models.Lot.category == category)
    if auction_id:
        q = q.filter(models.Lot.auction_id.in_(auction_id))
    if status or roi_status or bolo_only:
        q = q.join(models.Enrichment)
    if status:
        q = q.filter(models.Enrichment.status == status)
    if roi_status:
        q = q.filter(models.Enrichment.roi_status == roi_status)
    if bolo_only:
        q = q.filter(models.Enrichment.bolo_brand.isnot(None))

    from datetime import datetime
    from ..services import calibration
    now = datetime.now()
    # LIMIT/OFFSET without ORDER BY has no stability guarantee, and the UI
    # chains pages to assemble the full set. Under concurrent writes — which
    # is every bid refresh and every enrichment run — Postgres reshuffles
    # rows between pages: a 2,169-lot auction paged during updates came back
    # with 20 lots duplicated and 20 missing entirely. Order by primary key
    # so the pages tile. The client re-sorts for display anyway; this only
    # has to be deterministic.
    rows = q.order_by(models.Lot.id).offset(offset).limit(limit).all()
    house_ratios = calibration.ratios(db)
    for lot in rows:
        # Serve the auction's name and closed-state with the lot, so the UI
        # never has to guess from a separately-fetched auction list.
        lot.auction_name = lot.auction.name if lot.auction else None
        lot.auction_closed = bool(
            lot.auction and lot.auction.closing_date
            and lot.auction.closing_date < now)
        # The house's estimate calibration, right where its estimate shows —
        # the anchor loses its pull when its track record sits beside it.
        cal = house_ratios.get(lot.auction.auctioneer_id) if lot.auction else None
        lot.house_ratio = cal["ratio"] if cal else None
        lot.house_ratio_n = cal["n"] if cal else 0
    return rows


def flush_closed_now(db: Session, dry_run: bool = False) -> dict:
    """Delete every imported lot that can no longer be bid on — its own
    HiBid status says closed/sold, OR its whole auction has closed — then
    drop the now-empty closed auctions. Shared by the manual endpoint below
    and the 12-hourly maintenance loop (workers/maintenance.py). Permanent —
    enrichment results (the paid AI calls) go with the lots.

    Watched lots are never flushed. Won lots are kept for WON_RETENTION_DAYS
    after being marked — a won lot is resale inventory and its enrichment
    (identification, comps, resale value) is what the user needs to list the
    item (the 2026-09-19 auto-flush deleted 13 just-won webcast lots before
    this guard) — but a week later it's listed or it isn't, and the row ages
    out like any other closed lot."""
    from datetime import datetime, timedelta
    from sqlalchemy import func, or_
    from .auctions import purge_stale_auctions
    from ..services import calibration

    _CLOSED_STATUSES = ("CLOSED", "SOLD", "ENDED", "PASSED", "ARCHIVED")
    won_cutoff = datetime.now() - timedelta(days=WON_RETENTION_DAYS)
    doomed = (
        db.query(models.Lot, models.Auction.auctioneer_id)
          .join(models.Auction, models.Lot.auction_id == models.Auction.id)
          .filter(or_(
              (models.Auction.closing_date.isnot(None))
              & (models.Auction.closing_date < datetime.now()),
              func.upper(func.coalesce(models.Lot.status, "")).in_(_CLOSED_STATUSES),
          ))
          # coalesce: rows created before these columns existed hold NULL.
          # A won lot is only flushable once its retention window has passed;
          # a NULL won_at on a won row (pre-migration edge) never qualifies.
          .filter(or_(func.coalesce(models.Lot.won, False).is_(False),
                      models.Lot.won_at < won_cutoff),
                  func.coalesce(models.Lot.watched, False).is_(False))
          .all()
    )
    lot_ids = [lot.id for lot, _ in doomed]
    if dry_run:
        return {"lots": len(lot_ids), "dry_run": True}
    # Last chance to learn from these lots: estimate-vs-hammer observations
    # feed the per-house calibration, and the rows are about to be deleted.
    calibration.capture(db, doomed)
    if lot_ids:
        # No delete-cascade on the models, so enrichments go first.
        (db.query(models.Enrichment)
           .filter(models.Enrichment.lot_id.in_(lot_ids))
           .delete(synchronize_session=False))
        (db.query(models.Lot)
           .filter(models.Lot.id.in_(lot_ids))
           .delete(synchronize_session=False))
        db.commit()
    auctions_removed = purge_stale_auctions(db)
    return {"lots": len(lot_ids), "auctions": auctions_removed, "dry_run": False}


@router.post("/flush-closed")
def flush_closed(dry_run: bool = False, db: Session = Depends(get_db)):
    """Manual flush — dry_run=true only counts, so the UI can put a real
    number in its confirm dialog."""
    return flush_closed_now(db, dry_run=dry_run)


@router.post("/{lot_id}/watch", response_model=schemas.LotOut)
def set_watch(lot_id: str, watched: bool = True, db: Session = Depends(get_db)):
    """Toggle the closing-soon alert flag. Re-watching clears a previous
    alert timestamp so the lot can alert again (e.g. after an extension)."""
    lot = (db.query(models.Lot)
             .options(joinedload(models.Lot.enrichment))
             .filter(models.Lot.lot_id == lot_id).first())
    if not lot:
        raise HTTPException(status_code=404, detail="Lot not found")
    lot.watched = watched
    if watched:
        lot.closing_alert_sent_at = None
    db.commit()
    db.refresh(lot)
    return lot


@router.post("/{lot_id}/won", response_model=schemas.LotOut)
def set_won(lot_id: str, won: bool = True, db: Session = Depends(get_db)):
    """Mark a lot as won at auction (or unmark it). A won lot is inventory:
    the flush spares it for WON_RETENTION_DAYS, and the UI keeps it visible
    even with 'Hide closed' on. The app can't detect wins itself — no HiBid
    account linkage — so this button is how it finds out. Re-marking resets
    the retention clock."""
    from datetime import datetime
    lot = (db.query(models.Lot)
             .options(joinedload(models.Lot.enrichment))
             .filter(models.Lot.lot_id == lot_id).first())
    if not lot:
        raise HTTPException(status_code=404, detail="Lot not found")
    lot.won = won
    lot.won_at = datetime.now() if won else None
    db.commit()
    db.refresh(lot)
    return lot


@router.post("/{lot_id}/hide", response_model=schemas.LotOut)
def set_hidden(lot_id: str, hidden: bool = True, db: Session = Depends(get_db)):
    """Manually hide a lot from the items view (or unhide it). The row and
    its enrichment stay in the DB — this is a personal 'not interested'."""
    lot = (db.query(models.Lot)
             .options(joinedload(models.Lot.enrichment))
             .filter(models.Lot.lot_id == lot_id).first())
    if not lot:
        raise HTTPException(status_code=404, detail="Lot not found")
    lot.hidden = hidden
    db.commit()
    db.refresh(lot)
    return lot


@router.get("/{lot_id}", response_model=schemas.LotOut)
def get_lot(lot_id: str, db: Session = Depends(get_db)):
    lot = (
        db.query(models.Lot)
        .options(joinedload(models.Lot.enrichment))
        .filter(models.Lot.lot_id == lot_id)
        .first()
    )
    if not lot:
        raise HTTPException(status_code=404, detail="Lot not found")
    return lot


@router.post("", response_model=schemas.LotOut, status_code=201)
def create_lot(payload: schemas.LotCreate, db: Session = Depends(get_db)):
    existing = db.query(models.Lot).filter(models.Lot.lot_id == payload.lot_id).first()
    if existing:
        raise HTTPException(status_code=409, detail="Lot already exists")

    lot = models.Lot(**payload.model_dump())
    db.add(lot)
    db.flush()  # get lot.id before creating the enrichment row

    db.add(models.Enrichment(lot_id=lot.id, status="pending"))
    db.commit()
    db.refresh(lot)
    return lot
