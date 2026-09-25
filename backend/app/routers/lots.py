from datetime import timedelta
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_
from sqlalchemy.orm import Session, joinedload
from typing import Optional, List

from .. import models, schemas
from ..database import get_db
from ..services import twins
from ..services.boilerplate import is_boilerplate

router = APIRouter(prefix="/lots", tags=["lots"])


@router.get("/count")
def count_lots(
    category: Optional[str] = None,
    status: Optional[str] = None,
    auction_id: Optional[List[int]] = Query(None, description="repeatable — any of these auctions"),
    roi_status: Optional[str] = None,
    bolo_only: bool = False,
    priced_only: bool = False,
    flagged_only: bool = False,
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
    q = _visible_auctions(q, auction_id)
    q = _enrichment_filters(q, status, roi_status, bolo_only, priced_only,
                            flagged_only)
    return {"total": q.count()}


def _visible_auctions(q, auction_id):
    """Drop the lots of a dismissed auction.

    Hiding a sale has to take its listings with it - hiding the auction and
    leaving 187 of its lots in the inventory is not hiding it. Filtered
    rather than written onto each lot, so bringing the auction back brings
    its lots back unchanged.

    An explicit auction_id is honoured anyway: that is the auction-name
    click, and it is the only way back to a dismissed sale's own items.
    """
    if auction_id:
        return q
    return q.filter(models.Lot.auction.has(
        or_(models.Auction.hidden.is_(False), models.Auction.hidden.is_(None))))


def _enrichment_filters(q, status, roi_status, bolo_only, priced_only,
                        flagged_only=False):
    """The filters that live on the enrichment row, shared by the list and
    its count so the two can never disagree about what is in view."""
    if status or roi_status or bolo_only or priced_only or flagged_only:
        q = q.join(models.Enrichment)
    if status:
        q = q.filter(models.Enrichment.status == status)
    if roi_status:
        q = q.filter(models.Enrichment.roi_status == roi_status)
    if bolo_only:
        q = q.filter(models.Enrichment.bolo_brand.isnot(None))
    if priced_only:
        # "Priced" means a value exists, whatever tier produced it - the
        # Priced inventory tab is every lot the app has an opinion on.
        q = q.filter(models.Enrichment.est_resale.isnot(None))
    if flagged_only:
        # The worklist: every lot the user has personally said "this
        # valuation is wrong" about.
        q = q.filter(models.Enrichment.comp_flagged.is_(True))
    return q


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
              # Same scope as the bulk paths (enrichment._worth_pricing):
              # a pickup-only lot out of range is never priced.
              models.Lot.unreachable_pickup.isnot(True),
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
    priced_only: bool = Query(False, description="only lots with an est_resale"),
    flagged_only: bool = Query(False, description="only lots flagged as wrong comps"),
    include_comps: bool = Query(False, description="send each lot's comp records too"),
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
    q = _visible_auctions(q, auction_id)
    q = _enrichment_filters(q, status, roi_status, bolo_only, priced_only,
                            flagged_only)

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
        # A Canadian house that won't cross the border: the lot can be won
        # but never received. Served per lot so the items view can hide it.
        lot.auction_no_us_ship = bool(lot.auction and lot.auction.ships_to_us is False)
        # The house's estimate calibration, right where its estimate shows —
        # the anchor loses its pull when its track record sits beside it.
        cal = house_ratios.get(lot.auction.auctioneer_id) if lot.auction else None
        lot.house_ratio = cal["ratio"] if cal else None
        lot.house_ratio_n = cal["n"] if cal else 0
    if not include_comps:
        # The comp records are 60% of this response - 2.8 MB a page - and
        # they are only read when one row's evidence panel is opened, which
        # fetches that lot on its own (GET /lots/{lot_id} always carries
        # them). Cleared last, after every query above, and never committed:
        # the session is rolled back and closed when the request ends.
        for lot in rows:
            if lot.enrichment is not None:
                lot.enrichment.comps = None
    return rows


# How long past a lot's own closing time before the flush takes it (see
# flush_closed_now).
LOT_CLOSE_GRACE = timedelta(hours=1)


def flush_closed_now(db: Session, dry_run: bool = False) -> dict:
    """Delete every imported lot that can no longer be bid on — its own
    HiBid status says closed/sold, OR its whole auction has closed — then
    drop the now-empty closed auctions. Shared by the manual endpoint below
    and the 12-hourly maintenance loop (workers/maintenance.py). Permanent —
    enrichment results (the paid AI calls) go with the lots.

    A lot also counts as closed once its OWN closing time is LOT_CLOSE_GRACE
    past. Timed sales close lot by lot, hours before the auction's final
    close, and HiBid's per-lot status only reaches us through a bid
    refresh - which no longer runs on its own - so without this, lots whose
    time was up stayed "open" and were never flushed (727 of them on
    2026-09-25). The grace covers HiBid extending a lot when a bid lands in
    its last minutes.

    Watched lots are never flushed."""
    from datetime import datetime
    from sqlalchemy import and_, func, or_
    from .auctions import purge_stale_auctions
    from ..services import calibration

    _CLOSED_STATUSES = ("CLOSED", "SOLD", "ENDED", "PASSED", "ARCHIVED")
    now = datetime.now()
    auction_over = and_(models.Auction.closing_date.isnot(None),
                        models.Auction.closing_date < now)
    status_closed = func.upper(func.coalesce(models.Lot.status, "")).in_(_CLOSED_STATUSES)
    time_up = and_(models.Lot.closes_at.isnot(None),
                   models.Lot.closes_at < now - LOT_CLOSE_GRACE)
    rows = (
        db.query(models.Lot, models.Auction.auctioneer_id,
                 or_(auction_over, status_closed))
          .join(models.Auction, models.Lot.auction_id == models.Auction.id)
          .filter(or_(auction_over, status_closed, time_up))
          # coalesce: rows created before the column existed hold NULL.
          .filter(func.coalesce(models.Lot.watched, False).is_(False))
          .all()
    )
    doomed = [(lot, aid) for lot, aid, _ in rows]
    lot_ids = [lot.id for lot, _ in doomed]
    if dry_run:
        return {"lots": len(lot_ids), "dry_run": True}
    # Last chance to learn from these lots: estimate-vs-hammer observations
    # feed the per-house calibration, and the rows are about to be deleted.
    # Not from a lot known closed only by the clock: its bid is whatever it
    # was at the last refresh, not what it sold for, and a stale "hammer"
    # would bend the house's estimate ratio.
    calibration.capture(db, [(lot, aid) for lot, aid, confirmed in rows if confirmed])
    if lot_ids:
        # No delete-cascade on the models, so the children go first. The
        # price trail was added after this was written, and the first closed
        # auction whose lots carried one failed the whole flush on its
        # foreign key and rolled it back - after a day of re-pricing, that
        # was nearly every closed auction.
        (db.query(models.PriceObservation)
           .filter(models.PriceObservation.lot_id.in_(lot_ids))
           .delete(synchronize_session=False))
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


@router.post("/hide-boilerplate")
def hide_boilerplate(dry_run: bool = False, db: Session = Depends(get_db)):
    """Hide the house notices already on file.

    Imports drop them now, but the ones imported before that are still here,
    still priced - a returns policy valued at $56.70 - and still eligible
    for a re-price. Hidden rather than deleted: the enrichment rows are real
    history, and hiding is what already takes a lot out of the views and out
    of every bulk pricing path.
    """
    rows = (db.query(models.Lot)
              .filter(or_(models.Lot.hidden.is_(False),
                          models.Lot.hidden.is_(None))).all())
    todo = [r for r in rows if is_boilerplate(r.title)]
    if not dry_run and todo:
        (db.query(models.Lot)
           .filter(models.Lot.id.in_([r.id for r in todo]))
           .update({models.Lot.hidden: True}, synchronize_session=False))
        db.commit()
    return {"changed": len(todo), "dry_run": dry_run,
            "titles": [r.title for r in todo[:20]]}


@router.post("/{lot_id}/hide-like")
def hide_like(lot_id: str, hidden: bool = True, dry_run: bool = False,
              db: Session = Depends(get_db)):
    """Hide (or bring back) every lot that is the same product as this one.

    Hiding was the most-used action in the app and 57 of 68 of them came in
    bursts - the same thing dismissed four, six, nine times in a row,
    because a liquidation sale lists one product many times and tells the
    copies apart with an asset tag. This is one decision instead.

    dry_run returns the count and a few titles without changing anything, so
    the confirm can say what it is about to do. Nothing here is destructive:
    the lots are marked, not deleted, and unhiding is the same call with
    hidden=false.
    """
    lot = db.query(models.Lot).filter(models.Lot.lot_id == lot_id).first()
    if not lot:
        raise HTTPException(status_code=404, detail="Lot not found")
    key = twins.product_key(lot.title)
    if key is None:
        # Too generic to group on - "Pyrex" is a category, not a product.
        # Say so rather than quietly hiding the one lot and implying more.
        return {"key": None, "matched": 0, "changed": 0, "titles": [],
                "reason": "This title is too generic to match others on"}

    # Every title is normalised in Python rather than SQL, because the key
    # drops a trailing asset tag and that is not a regexp worth writing
    # twice. A few thousand titles is one cheap query.
    rows = db.query(models.Lot.id, models.Lot.title, models.Lot.hidden).all()
    same = [r for r in rows if twins.product_key(r[1]) == key]
    todo = [r for r in same if bool(r[2]) != hidden]
    if dry_run:
        return {"key": key, "matched": len(same), "changed": len(todo),
                "titles": [r[1] for r in todo[:5]]}
    if todo:
        (db.query(models.Lot)
           .filter(models.Lot.id.in_([r[0] for r in todo]))
           .update({models.Lot.hidden: hidden}, synchronize_session=False))
        db.commit()
    return {"key": key, "matched": len(same), "changed": len(todo),
            "titles": [r[1] for r in todo[:5]]}


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
