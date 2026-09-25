from datetime import datetime, timezone
from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..services import jobs
from ..workers.enrich import _apply_roi

router = APIRouter(prefix="/lots", tags=["enrichment"])


def _not_hidden():
    """Lots the user hide-dismissed never earn another cent or comp lookup —
    every bulk path filters on this. Unhiding puts a lot back in scope."""
    return or_(models.Lot.hidden.is_(False), models.Lot.hidden.is_(None))


def _worth_pricing():
    """What every bulk pricing path may spend on: not hidden by the user,
    not a pickup-only lot in an auction outside the scan radius, and not a
    lot whose own closing time has passed - nothing there can be bought."""
    return and_(_not_hidden(),
                or_(models.Lot.unreachable_pickup.is_(False),
                    models.Lot.unreachable_pickup.is_(None)),
                or_(models.Lot.closes_at.is_(None),
                    models.Lot.closes_at >= datetime.now()))


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
    # The user asked for a fresh look at THIS lot, and a fresh look includes
    # a fresh audit. A lot with any verdict is never re-audited, and the
    # verdict only clears when the value changes - so a LeBron rookie whose
    # re-price came back at the same $56 kept an audit verdict produced
    # under the old rules, with no way to shake it. Bulk paths leave
    # verdicts alone; one click on one lot is a different intent.
    lot.enrichment.gold_check = None
    lot.enrichment.gold_check_note = None
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
def reprice(auction_id: int | None = None, weak_only: bool = False,
            unpriced_only: bool = False, dry_run: bool = False,
            auction_ids: list[int] | None = Query(None),
            category: str | None = None,
            payload: schemas.RepriceRequest | None = Body(None),
            db: Session = Depends(get_db)):
    """Recompute comps + ROI for enriched lots using current pricing rules.

    Reuses the AI title and verdict already stored, so it's the right way to
    apply a pricing change to old data. Near-free: the only model spend is
    the second-opinion audit on lots that come out GOLD MINE (~half a cent
    each, see workers/enrich._verify_gold).

    weak_only limits it to lots priced from asking listings - the ones an
    outage or a bad query left behind - and first purges every cached EMPTY
    sold answer, because otherwise the re-price is handed the same empties
    back for a week and changes nothing. dry_run reports both counts and
    spends nothing, so the request cost is known before it is paid.
    """
    from ..services import pricing
    q = (db.query(models.Lot.id)
           .join(models.Enrichment)
           .filter(_worth_pricing()))
    if unpriced_only:
        # Never-priced lots, searched on their raw auction titles: the
        # comps-only first pass. No AI spend - the worker falls back to
        # lot.title when there is no enriched one - so the only cost is one
        # SoldComps request per lot. Open auctions only; a price on a closed
        # lot buys nothing. The status stays pending, so the AI pass still
        # knows these lots are owed a condition judgement.
        q = (q.join(models.Auction, models.Lot.auction_id == models.Auction.id)
               .filter(models.Enrichment.est_resale.is_(None),
                       (models.Auction.closing_date.is_(None))
                       | (models.Auction.closing_date >= datetime.now())))
    elif payload and payload.lot_ids:
        # An explicit selection: the user ticked these. No scope filter -
        # a lot with no AI title is searched on its raw one, a lot with a
        # value already is searched again. Their intent, their request cost.
        q = q.filter(models.Lot.lot_id.in_(payload.lot_ids))
    else:
        q = q.filter(models.Enrichment.enriched_title.isnot(None))
    if auction_id:
        q = q.filter(models.Lot.auction_id == auction_id)
    # The on-screen scope. A production dry run of the comps-only pass came
    # back with 8,262 lots when the user was thinking of about 2,000: the
    # whole inventory, not the auctions and category they had in view. The
    # button now sends what the screen shows, so the request count quoted
    # is the one they meant.
    if auction_ids:
        q = q.filter(models.Lot.auction_id.in_(auction_ids))
    if category:
        q = q.filter(models.Lot.category == category)
    if weak_only:
        q = q.filter(models.Enrichment.price_source.ilike("active%"))
    lot_ids = [row[0] for row in q.all()]
    if dry_run:
        return {"repricing": len(lot_ids), "dry_run": True,
                "cache_empties": pricing.count_empty_sold_cache() if weak_only else 0,
                # About one request per lot: the first query variant answers
                # nearly every time, and the durable cache absorbs repeats.
                "requests_estimate": len(lot_ids) if unpriced_only else None}
    if not lot_ids:
        return {"repricing": 0}
    # Deploys resume orphaned reprices, so stacking a second one is easy to
    # do by accident — and N concurrent reprices burn N× the comp lookups.
    if jobs.has_pending("reprice"):
        # Only a DUPLICATE is worth refusing now. The worker serialises long
        # jobs by itself, so an unrelated one being busy is no reason to drop
        # this request — it would simply wait its turn.
        return {"repricing": 0, "already_running": True}
    # Purge BEFORE enqueueing, so the worker never sees the stale empties.
    purged = pricing.purge_empty_sold_cache() if weak_only else 0
    jobs.enqueue("reprice", "Re-pricing lots with current comp rules",
                 total=len(lot_ids), payload={"lot_ids": lot_ids})
    return {"repricing": len(lot_ids), "cache_purged": purged}


@router.post("/audit-golds", status_code=202)
def audit_golds(db: Session = Depends(get_db)):
    """Queue the second-opinion audit for every GOLD MINE the checker never
    saw — and for every thin-evidence lot with enough profit on the table
    to be worth promoting (see workers/enrich.run_audit_sweep, whose
    filters this count must mirror or the job never enqueues for them).
    Costs ~half a cent per lot, nothing when none need it."""
    from ..workers.enrich import PROMOTE_MIN_PROFIT
    n = (db.query(models.Lot.id)
           .join(models.Enrichment, models.Enrichment.lot_id == models.Lot.id)
           .join(models.Auction, models.Lot.auction_id == models.Auction.id)
           .filter(models.Enrichment.gold_check.is_(None),
                   _worth_pricing(),
                   (models.Auction.closing_date.is_(None))
                   | (models.Auction.closing_date >= datetime.now()),
                   (models.Enrichment.roi_status == "GOLD MINE")
                   | ((models.Enrichment.roi_status == "PASS")
                      & models.Enrichment.roi_reason.like("only %")
                      & (models.Enrichment.profit >= PROMOTE_MIN_PROFIT)))
           .count())
    if not n:
        return {"auditing": 0}
    if jobs.has_pending("audit-golds"):
        return {"auditing": 0, "already_running": True}
    jobs.enqueue("audit-golds", "Auditing unchecked golds", total=n)
    return {"auditing": n}


@router.post("/match-twins")
def match_twins(dry_run: bool = True,
                auction_id: list[int] | None = Query(None, description="limit to these auctions"),
                db: Session = Depends(get_db)):
    """Make every group of same-title lots carry one value - the most
    recently priced lot's. Free: nothing is looked up, values are copied.
    A dry run (the default) only counts what would change."""
    from ..workers.enrich import match_twins as _match
    return _match(db, dry_run=dry_run, auction_ids=auction_id)


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
                   _worth_pricing(),
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
    q = (db.query(models.Lot.id).join(models.Enrichment)
           .filter(models.Enrichment.status == "success",
                   _worth_pricing(),
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
                _worth_pricing(),
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
