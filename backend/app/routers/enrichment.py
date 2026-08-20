from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.orm import Session

from .. import models
from ..database import get_db
from ..workers.enrich import run_enrichment

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
