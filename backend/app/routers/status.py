"""GET /status — everything happening server-side right now, for the top bar."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from .. import models
from ..database import get_db
from ..services import jobs

router = APIRouter(tags=["status"])


@router.get("/status")
def get_status(db: Session = Depends(get_db)):
    """Active scans/imports plus the enrichment queue's live state."""
    queued = (
        db.query(models.Enrichment)
        .filter(models.Enrichment.status == "queued")
        .count()
    )
    # The lot actually being worked right now publishes a stage string.
    working = (
        db.query(models.Enrichment, models.Lot.title)
        .join(models.Lot, models.Lot.id == models.Enrichment.lot_id)
        .filter(models.Enrichment.status == "queued",
                models.Enrichment.progress.isnot(None))
        .first()
    )
    enrichment = {"queued": queued, "stage": None, "lot_title": None}
    if working:
        enrichment["stage"] = working[0].progress
        enrichment["lot_title"] = working[1]

    return {"jobs": jobs.active(), "enrichment": enrichment}
