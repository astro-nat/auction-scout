from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, joinedload
from typing import Optional, List

from .. import models, schemas
from ..database import get_db

router = APIRouter(prefix="/lots", tags=["lots"])


@router.get("/count")
def count_lots(
    category: Optional[str] = None,
    status: Optional[str] = None,
    auction_id: Optional[int] = None,
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
        q = q.filter(models.Lot.auction_id == auction_id)
    if status or roi_status or bolo_only:
        q = q.join(models.Enrichment)
    if status:
        q = q.filter(models.Enrichment.status == status)
    if roi_status:
        q = q.filter(models.Enrichment.roi_status == roi_status)
    if bolo_only:
        q = q.filter(models.Enrichment.bolo_brand.isnot(None))
    return {"total": q.count()}


@router.get("", response_model=List[schemas.LotOut])
def list_lots(
    category: Optional[str] = None,
    status: Optional[str] = Query(None, description="pending | queued | success | failed"),
    auction_id: Optional[int] = None,
    roi_status: Optional[str] = Query(None, description="GOLD MINE | PASS"),
    bolo_only: bool = False,
    include_closed: bool = False,
    limit: int = 2000,
    offset: int = 0,
    db: Session = Depends(get_db),
):
    # Imported lots are kept visible even after their auction closes — the
    # enrichment work is yours, and a vanished lot looks like data loss. The
    # row is marked closed instead (see auction_closed below).
    q = (db.query(models.Lot)
           .options(joinedload(models.Lot.enrichment),
                    joinedload(models.Lot.auction)))
    if category:
        q = q.filter(models.Lot.category == category)
    if auction_id:
        q = q.filter(models.Lot.auction_id == auction_id)
    if status or roi_status or bolo_only:
        q = q.join(models.Enrichment)
    if status:
        q = q.filter(models.Enrichment.status == status)
    if roi_status:
        q = q.filter(models.Enrichment.roi_status == roi_status)
    if bolo_only:
        q = q.filter(models.Enrichment.bolo_brand.isnot(None))

    from datetime import datetime
    now = datetime.now()
    rows = q.offset(offset).limit(limit).all()
    for lot in rows:
        # Serve the auction's name and closed-state with the lot, so the UI
        # never has to guess from a separately-fetched auction list.
        lot.auction_name = lot.auction.name if lot.auction else None
        lot.auction_closed = bool(
            lot.auction and lot.auction.closing_date
            and lot.auction.closing_date < now)
    return rows


@router.post("/flush-closed")
def flush_closed(dry_run: bool = False, db: Session = Depends(get_db)):
    """Delete every imported lot whose auction has closed, then drop the
    now-empty closed auctions from the list.

    dry_run=true only counts, so the UI can put a real number in its
    confirm dialog. The delete is permanent — enrichment results (the
    paid AI calls) go with the lots.
    """
    from datetime import datetime
    from .auctions import purge_stale_auctions

    lot_ids = [
        row[0] for row in
        db.query(models.Lot.id)
          .join(models.Auction, models.Lot.auction_id == models.Auction.id)
          .filter(models.Auction.closing_date.isnot(None),
                  models.Auction.closing_date < datetime.now())
          .all()
    ]
    if dry_run:
        return {"lots": len(lot_ids), "dry_run": True}
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
