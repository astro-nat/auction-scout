from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..workers.enrich import run_enrichment, run_inspection, _apply_roi

router = APIRouter(prefix="/lots", tags=["enrichment"])


@router.post("/{lot_id}/enrich", status_code=202)
def enrich_lot(lot_id: str, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    lot = db.query(models.Lot).filter(models.Lot.lot_id == lot_id).first()
    if not lot:
        raise HTTPException(status_code=404, detail="Lot not found")

    lot.enrichment.status = "queued"
    db.commit()

    # Returns immediately — the actual AI call happens after the response is sent.
    # This is the fix for the Streamlit failure mode: nothing here blocks on the API call.
    background_tasks.add_task(run_enrichment, lot.id)

    return {"lot_id": lot_id, "status": "queued"}


@router.post("/{lot_id}/inspect", status_code=202)
def inspect_lot(lot_id: str, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """Itemized vision pass for mixed lots — identify and price each item in
    the photo individually. Costlier than /enrich (one comp lookup per item)."""
    lot = db.query(models.Lot).filter(models.Lot.lot_id == lot_id).first()
    if not lot:
        raise HTTPException(status_code=404, detail="Lot not found")
    if not (lot.thumbnail_url or lot.hd_thumbnail_url):
        raise HTTPException(status_code=422, detail="Lot has no image to inspect")

    lot.enrichment.status = "queued"
    db.commit()
    background_tasks.add_task(run_inspection, lot.id)
    return {"lot_id": lot_id, "status": "queued"}


@router.patch("/{lot_id}/enrichment", response_model=schemas.LotOut)
def patch_enrichment(lot_id: str, payload: schemas.EnrichmentPatch,
                     db: Session = Depends(get_db)):
    """Apply user corrections. Each corrected field is recorded in
    user_overrides so re-running enrichment never overwrites it. ROI is
    recomputed when the correction changes the resale estimate or verdict."""
    lot = (
        db.query(models.Lot)
        .filter(models.Lot.lot_id == lot_id)
        .first()
    )
    if not lot:
        raise HTTPException(status_code=404, detail="Lot not found")

    e = lot.enrichment
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(status_code=422, detail="No fields to update")

    overrides = set(e.user_overrides or [])
    for field, value in changes.items():
        if field == "logistics_ease":
            lot.logistics_ease = value  # lives on the Lot, not the enrichment
        else:
            setattr(e, field, value)
        overrides.add(field)
    e.user_overrides = sorted(overrides)

    if changes.keys() & {"est_resale", "verdict", "logistics_ease"}:
        _apply_roi(lot, e)

    db.commit()
    db.refresh(lot)
    return lot
