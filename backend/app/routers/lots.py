from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, joinedload
from typing import Optional, List

from .. import models, schemas
from ..database import get_db

router = APIRouter(prefix="/lots", tags=["lots"])


@router.get("", response_model=List[schemas.LotOut])
def list_lots(
    category: Optional[str] = None,
    status: Optional[str] = Query(None, description="pending | queued | success | failed"),
    auction_id: Optional[int] = None,
    roi_status: Optional[str] = Query(None, description="GOLD MINE | PASS"),
    bolo_only: bool = False,
    limit: int = 2000,
    offset: int = 0,
    db: Session = Depends(get_db),
):
    q = db.query(models.Lot).options(joinedload(models.Lot.enrichment))
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
    return q.offset(offset).limit(limit).all()


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
