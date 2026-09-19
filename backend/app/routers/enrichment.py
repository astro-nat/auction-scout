from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..services import jobs
from ..workers.enrich import _apply_roi

router = APIRouter(prefix="/lots", tags=["enrichment"])


@router.post("/{lot_id}/enrich", status_code=202)
def enrich_lot(lot_id: str, db: Session = Depends(get_db)):
    lot = db.query(models.Lot).filter(models.Lot.lot_id == lot_id).first()
    if not lot:
        raise HTTPException(status_code=404, detail="Lot not found")

    lot.enrichment.status = "queued"
    lot.enrichment.queued_task = "enrich"
    lot.enrichment.queued_at = datetime.now(timezone.utc)
    lot.enrichment.queue_rank = 0
    lot.enrichment.claimed_at = None
    db.commit()

    # Returns immediately: the worker process picks this up within a poll.
    # Nothing here blocks on the AI call — that was the Streamlit failure mode,
    # and running it in the API process was the one after that.
    return {"lot_id": lot_id, "status": "queued"}


@router.post("/enrich-batch", status_code=202)
def enrich_batch(payload: schemas.EnrichBatchRequest, db: Session = Depends(get_db)):
    """Queue enrichment for an explicit, ordered list of lots — the frontend
    sends what's visible on screen, top row first, so the user's current view
    gets processed before anything else. Already-successful lots are skipped.

    One light SELECT plus batched UPDATEs, not ORM objects: the old per-lot
    loop lazy-loaded each enrichment (two queries a lot), so queueing an
    800-lot "Enrich all" took long enough to read as the button doing
    nothing at all."""
    rows = (
        db.query(models.Lot.lot_id, models.Enrichment.id)
        .join(models.Enrichment, models.Enrichment.lot_id == models.Lot.id)
        .filter(models.Lot.lot_id.in_(payload.lot_ids),
                models.Enrichment.status.in_(["pending", "failed"]))
        .all()
    )
    by_lot = {lot_id: enrichment_id for lot_id, enrichment_id in rows}
    queued_at = datetime.now(timezone.utc)
    # The caller sent lot_ids in the order they appear on screen; queue_rank
    # keeps it, because the picking now happens in another process.
    mappings = [
        {"id": by_lot[lot_id], "status": "queued", "queued_task": "enrich",
         "queued_at": queued_at, "queue_rank": rank, "claimed_at": None}
        for rank, lot_id in enumerate(i for i in payload.lot_ids if i in by_lot)
    ]
    if mappings:
        db.bulk_update_mappings(models.Enrichment, mappings)
        db.commit()
    return {"queued": len(mappings)}


@router.post("/reprice", status_code=202)
def reprice(auction_id: int | None = None,
            db: Session = Depends(get_db)):
    """Recompute comps + ROI for enriched lots using current pricing rules.

    Reuses the AI title and verdict already stored, so it's the right way to
    apply a pricing change to old data. Near-free: the only model spend is
    the second-opinion audit on lots that come out GOLD MINE (~half a cent
    each, see workers/enrich._verify_gold).
    """
    q = (db.query(models.Lot.id)
           .join(models.Enrichment)
           .filter(models.Enrichment.enriched_title.isnot(None)))
    if auction_id:
        q = q.filter(models.Lot.auction_id == auction_id)
    lot_ids = [row[0] for row in q.all()]
    if not lot_ids:
        return {"repricing": 0}
    # Deploys resume orphaned reprices, so stacking a second one is easy to
    # do by accident — and N concurrent reprices burn N× the comp lookups.
    if jobs.has_pending("reprice"):
        # Only a DUPLICATE is worth refusing now. The worker serialises long
        # jobs by itself, so an unrelated one being busy is no reason to drop
        # this request — it would simply wait its turn.
        return {"repricing": 0, "already_running": True}
    jobs.enqueue("reprice", "Re-pricing lots with current comp rules",
                 total=len(lot_ids), payload={"lot_ids": lot_ids})
    return {"repricing": len(lot_ids)}


@router.post("/enrich-category", status_code=202)
def enrich_category(category: str, skip_hard: bool = False, dry_run: bool = False,
                    db: Session = Depends(get_db)):
    """Queue enrichment for every pending/failed lot in one category, across
    ALL imported auctions — but only open ones: pricing a lot you can no
    longer bid on spends money on nothing. Same contract as an auction's
    enrich-all: already-successful lots are skipped, skip_hard leaves out
    HARD-to-ship lots, dry_run only counts so the UI can show cost first."""
    q = (db.query(models.Lot.id).join(models.Enrichment)
           .join(models.Auction, models.Lot.auction_id == models.Auction.id)
           .filter(models.Lot.category == category,
                   models.Enrichment.status.in_(["pending", "failed"]),
                   (models.Auction.closing_date.is_(None))
                   | (models.Auction.closing_date >= datetime.now())))
    if skip_hard:
        q = q.filter(models.Lot.logistics_ease != "HARD")
    lot_ids = [row[0] for row in q.all()]
    if dry_run:
        return {"category": category, "lots": len(lot_ids), "dry_run": True}
    if not lot_ids:
        return {"category": category, "queued": 0}
    db.query(models.Enrichment).filter(
        models.Enrichment.lot_id.in_(lot_ids)
    ).update({"status": "queued", "queued_task": "enrich",
              "queued_at": datetime.now(timezone.utc),
              "queue_rank": 0, "claimed_at": None},
             synchronize_session=False)
    db.commit()
    return {"category": category, "queued": len(lot_ids)}


@router.post("/re-enrich-blind", status_code=202)
def re_enrich_blind(auction_id: int | None = None, dry_run: bool = False,
                    db: Session = Depends(get_db)):
    """Re-queue lots that were 'enriched' while the AI was unreachable.

    A dead API key doesn't fail a lot — the AI pass just silently returns
    nothing, comps run against the raw auction title, and the lot lands on
    'success' with no identification and no condition verdict (the 2026-09-19
    key outage produced 171 of these). Blind = success, no ai_source, no
    enriched title, and no retail-in-title price standing in. Re-queueing
    sends them through the full pipeline again."""
    from sqlalchemy import or_
    q = (db.query(models.Lot.id).join(models.Enrichment)
           .filter(models.Enrichment.status == "success",
                   or_(models.Enrichment.ai_source.is_(None),
                       models.Enrichment.ai_source == "none"),
                   models.Enrichment.enriched_title.is_(None),
                   or_(models.Enrichment.price_source.is_(None),
                       ~models.Enrichment.price_source.like("retail $%"))))
    if auction_id:
        q = q.filter(models.Lot.auction_id == auction_id)
    lot_ids = [row[0] for row in q.all()]
    if dry_run:
        return {"lots": len(lot_ids), "dry_run": True}
    if not lot_ids:
        return {"queued": 0}
    db.query(models.Enrichment).filter(
        models.Enrichment.lot_id.in_(lot_ids)
    ).update({"status": "queued", "queued_task": "enrich",
              "queued_at": datetime.now(timezone.utc),
              "queue_rank": 0, "claimed_at": None},
             synchronize_session=False)
    db.commit()
    return {"queued": len(lot_ids)}


@router.post("/reinspect-no-comps", status_code=202)
def reinspect_no_comps(dry_run: bool = False,
                       db: Session = Depends(get_db)):
    """Re-run itemized inspection on every enriched lot that still has no
    usable price (est_resale is NULL) — the inspect strategy now falls back
    to the AI's own value estimate, so a re-run can put numbers on these.
    Only open auctions (a price on a closed lot buys nothing) and only lots
    with an image. dry_run counts, so the caller can show cost first."""
    from datetime import datetime
    rows = (
        db.query(models.Lot).join(models.Enrichment)
        .join(models.Auction, models.Lot.auction_id == models.Auction.id)
        .filter(models.Enrichment.status == "success",
                models.Enrichment.est_resale.is_(None),
                (models.Auction.closing_date.is_(None))
                | (models.Auction.closing_date >= datetime.now()),
                (models.Lot.fullsize_url.isnot(None))
                | (models.Lot.thumbnail_url.isnot(None)))
        .all()
    )
    if dry_run:
        return {"lots": len(rows), "dry_run": True}
    queued_at = datetime.now(timezone.utc)
    for rank, lot in enumerate(rows):
        lot.enrichment.status = "queued"
        lot.enrichment.queued_task = "inspect"
        lot.enrichment.queued_at = queued_at
        lot.enrichment.queue_rank = rank
        lot.enrichment.claimed_at = None
    db.commit()
    return {"queued": len(rows)}


@router.post("/{lot_id}/inspect", status_code=202)
def inspect_lot(lot_id: str, db: Session = Depends(get_db)):
    """Itemized vision pass for mixed lots — identify and price each item in
    the photo individually. Costlier than /enrich (one comp lookup per item)."""
    lot = db.query(models.Lot).filter(models.Lot.lot_id == lot_id).first()
    if not lot:
        raise HTTPException(status_code=404, detail="Lot not found")
    if not (lot.thumbnail_url or lot.hd_thumbnail_url):
        raise HTTPException(status_code=422, detail="Lot has no image to inspect")

    lot.enrichment.status = "queued"
    lot.enrichment.queued_task = "inspect"
    # rank 0: a lot the user just clicked jumps the batch queued behind it.
    lot.enrichment.queued_at = datetime.now(timezone.utc)
    lot.enrichment.queue_rank = 0
    lot.enrichment.claimed_at = None
    db.commit()
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
    if "est_resale" in changes:
        # The gold audit certified the OLD number; a hand-set price stands on
        # the user's authority and is never re-audited.
        e.gold_check = None
        e.gold_check_note = None

    if changes.keys() & {"est_resale", "verdict", "logistics_ease"}:
        _apply_roi(lot, e)

    db.commit()
    db.refresh(lot)
    return lot
