"""Auction discovery + lot import.

POST /auctions/scan          — discover open HiBid auctions near a zip, upsert them
POST /auctions/{id}/import   — pull all open lots for one auction into Postgres
POST /auctions/{id}/enrich-all — queue enrichment for every un-enriched lot
GET  /auctions               — list what we know about
"""

from datetime import datetime, timezone  # noqa: F401 — datetime used in filters
from typing import List

from fastapi import APIRouter, Depends, HTTPException

from .. import models, schemas
from ..database import get_db
from ..services import dismissed, favorites, hibid, jobs
from ..services import shipping
from sqlalchemy import or_, and_, func
from sqlalchemy.orm import Session

router = APIRouter(prefix="/auctions", tags=["auctions"])


@router.get("", response_model=List[schemas.AuctionOut])
def list_auctions(include_closed: bool = False, include_hidden: bool = False,
                  db: Session = Depends(get_db)):
    """Open auctions (closed ones stay in the DB but drop off the list),
    each annotated with its gold-mine tally: how many enriched lots are
    GOLD MINEs and their summed potential profit."""
    from sqlalchemy import func, or_
    q = db.query(models.Auction)
    if not include_closed:
        # Hide closed auctions — unless you imported lots from them, in which
        # case the card has to stay or your items look orphaned.
        imported = (db.query(models.Lot.auction_id)
                      .filter(models.Lot.auction_id.isnot(None))
                      .distinct())
        q = q.filter(or_(models.Auction.closing_date.is_(None),
                         models.Auction.closing_date >= datetime.now(),
                         models.Auction.id.in_(imported)))
    if not include_hidden:
        # Dismissed auctions stay out unless you ask for them — except ones
        # you've imported lots from, where the card has to stay or the items
        # look orphaned.
        imported_ids = (db.query(models.Lot.auction_id)
                          .filter(models.Lot.auction_id.isnot(None)).distinct())
        q = q.filter(or_(models.Auction.hidden.is_(False),
                         models.Auction.hidden.is_(None),
                         models.Auction.id.in_(imported_ids)))
        # Forgotten sales are out unconditionally — "never see it again" beats
        # the imported-lots carve-out above, and the lots themselves keep
        # their auction name either way (routers/lots.py reads it per lot).
        forgotten = dismissed.ids(db)
        if forgotten:
            q = q.filter(or_(models.Auction.hibid_id.is_(None),
                             models.Auction.hibid_id.notin_(forgotten)))
    auctions = q.order_by(models.Auction.closing_date).all()
    # Watched auction houses first, each group still soonest-closing first.
    # A house you've starred is one you already trust, so its sales are worth
    # seeing before a stranger's that happens to close an hour earlier.
    starred = favorites.ids(db)
    auctions.sort(key=lambda a: (
        a.auctioneer_id not in starred,
        a.closing_date or datetime.max,
    ))
    return _attach_stats(db, auctions)


@router.get("/favorites")
def list_favorites(db: Session = Depends(get_db)):
    """Auction houses pinned to the top of the list."""
    rows = (db.query(models.FavoriteAuctioneer)
              .order_by(models.FavoriteAuctioneer.name).all())
    return [{"auctioneer_id": r.auctioneer_id, "name": r.name} for r in rows]


@router.post("/favorites", status_code=201)
def add_favorite(payload: dict, db: Session = Depends(get_db)):
    """Favourite an auction house.

    Takes whatever's to hand: a pasted company URL
    ("https://hibid.com/company/149798/budget-barn"), a bare id, or an
    auction_id already in the database.
    """
    company_id = favorites.parse_company_id(payload.get("company") or
                                            payload.get("auctioneer_id") or "")
    name = payload.get("name")
    if company_id is None and payload.get("auction_id"):
        auction = (db.query(models.Auction)
                     .filter(models.Auction.id == payload["auction_id"]).first())
        if auction and auction.auctioneer_id:
            company_id, name = auction.auctioneer_id, auction.auctioneer
    if company_id is None:
        raise HTTPException(
            status_code=422,
            detail="Need a HiBid company URL, a company id, or an auction_id "
                   "whose auctioneer is known")
    row = favorites.add(db, company_id, name)
    return {"auctioneer_id": row.auctioneer_id, "name": row.name}


@router.delete("/favorites/{company_id}")
def remove_favorite(company_id: int, db: Session = Depends(get_db)):
    return {"removed": favorites.remove(db, company_id)}


def _attach_stats(db: Session, auctions: list) -> list:
    """Annotate auction rows with gold-mine tallies and pipeline counts.

    Every endpoint that returns AuctionOut rows must go through here — a
    response with the schema's default zeros reads as "Not imported yet"
    in the UI even when the auction has a thousand lots in the database.
    """
    from sqlalchemy import case, func
    gold = dict()
    rows = (
        db.query(models.Lot.auction_id, func.count(models.Enrichment.id),
                 func.coalesce(func.sum(models.Enrichment.profit), 0))
        .join(models.Enrichment, models.Enrichment.lot_id == models.Lot.id)
        .filter(models.Enrichment.roi_status == "GOLD MINE")
        .group_by(models.Lot.auction_id)
        .all()
    )
    for auction_id, count, profit in rows:
        gold[auction_id] = (count, profit)

    # Per-auction pipeline state, so a card can say what's actually in the DB.
    stat_rows = (
        db.query(
            models.Lot.auction_id,
            func.count(models.Lot.id),
            func.count(case((models.Enrichment.status == "success", 1))),
            func.count(case((models.Enrichment.status.in_(["pending", "queued"]), 1))),
            func.count(case((models.Enrichment.status == "failed", 1))),
            func.count(case((models.Enrichment.ai_source == "vision-itemized", 1))),
            func.count(case(((models.Lot.logistics_ease == "HARD")
                             & models.Enrichment.status.in_(["pending", "failed"]), 1))),
            # What the auction's "Price N" (comps) button would look up:
            # no value yet, not hidden, not an unreachable pickup lot.
            func.count(case((models.Enrichment.est_resale.is_(None)
                             & models.Enrichment.status.in_(["pending", "failed"])
                             & models.Lot.hidden.isnot(True)
                             & models.Lot.unreachable_pickup.isnot(True), 1))),
        )
        .outerjoin(models.Enrichment, models.Enrichment.lot_id == models.Lot.id)
        .group_by(models.Lot.auction_id)
        .all()
    )
    stats = {r[0]: r[1:] for r in stat_rows}

    starred = favorites.ids(db)
    from ..services import calibration
    house_ratios = calibration.ratios(db)
    for a in auctions:
        a.gold_count, a.gold_profit = gold.get(a.id, (0, 0))
        (a.lots_imported, a.lots_enriched, a.lots_pending,
         a.lots_failed, a.lots_inspected,
         a.lots_hard_pending, a.lots_unpriced) = stats.get(a.id, (0, 0, 0, 0, 0, 0, 0))
        a.favorite = a.auctioneer_id in starred
        # This house's estimate-to-hammer track record, from its own closed
        # lots — how much to discount everything it claims.
        cal = house_ratios.get(a.auctioneer_id)
        a.estimate_ratio = cal["ratio"] if cal else None
        a.estimate_ratio_n = cal["n"] if cal else 0
    return auctions


def purge_stale_auctions(db: Session) -> int:
    """Delete closed auctions we never imported any lots from.

    Every scan appends whatever HiBid returns, so the table grows without
    bound. An auction that has closed AND has no lots holds nothing worth
    keeping — no enrichment, no corrections, no history. Auctions with lots
    are always kept, closed or not.
    """
    stale_ids = [
        row[0] for row in
        db.query(models.Auction.id)
          .outerjoin(models.Lot, models.Lot.auction_id == models.Auction.id)
          .filter(models.Auction.closing_date.isnot(None),
                  models.Auction.closing_date < datetime.now(),
                  models.Lot.id.is_(None))
          .all()
    ]
    if stale_ids:
        (db.query(models.Auction)
           .filter(models.Auction.id.in_(stale_ids))
           .delete(synchronize_session=False))
        db.commit()
    return len(stale_ids)


@router.post("/purge-stale")
def purge_stale(db: Session = Depends(get_db)):
    """Manually drop closed auctions with no imported lots."""
    return {"removed": purge_stale_auctions(db)}


@router.post("/analyze-shipping", status_code=202)
async def analyze_shipping(dry_run: bool = False,
                           force: bool = False,
                           db: Session = Depends(get_db)):
    """AI-read each open auction's shipping info + terms and store a rough
    per-item shipping cost estimate (see workers.enrich.run_ship_analysis).

    dry_run=true only counts, so the UI can put a real number and cost in
    its confirm dialog. force=true re-analyzes auctions already read.
    """
    q = (db.query(models.Auction)
           .filter(models.Auction.hibid_id.isnot(None))
           .filter((models.Auction.closing_date.is_(None))
                   | (models.Auction.closing_date >= datetime.now())))
    if not force:
        # Never read: read it. A Canadian house read before the border
        # question existed, or whose terms left it open: ask again.
        q = q.filter(or_(
            models.Auction.ship_analyzed_at.is_(None),
            and_(func.upper(models.Auction.state).in_(shipping.CANADIAN_PROVINCES),
                 models.Auction.ships_to_us.is_(None))))
    targets = q.all()
    if dry_run:
        return {"auctions": len(targets), "dry_run": True}
    if not targets:
        return {"auctions": 0, "queued": False}

    # Hand the worker ids only — it fetches the shipping/terms text itself,
    # which keeps this response instant and makes the run resumable (the id
    # list persists on the job row; texts would bloat it).
    jobs.enqueue("ship-analysis", "Reading shipping policies",
                 total=len(targets),
                 payload={"auction_ids": [a.id for a in targets]})
    return {"auctions": len(targets), "queued": True}


@router.post("/refresh-bids", status_code=202)
def refresh_bids(window_hours: float | None = None,
                 db: Session = Depends(get_db)):
    """Re-pull current bids from HiBid and recompute ROI at the new bids.
    Free — no AI calls.

    Limited to auctions closing inside BID_REFRESH_WINDOW_HOURS, same as the
    hourly loop. Pass window_hours=0 to refresh everything still open.
    """
    from ..workers.refresh import auctions_due_for_bid_refresh
    ids = auctions_due_for_bid_refresh(db, window_hours)
    if not ids:
        return {"auctions": 0, "queued": False,
                "reason": "nothing closing inside the refresh window"}
    jobs.enqueue("bid-refresh", "Refreshing current bids",
                 total=len(ids), payload={"auction_ids": ids})
    return {"auctions": len(ids), "queued": True}


@router.get("/categories")
async def list_categories():
    """HiBid's top-level category tree, for the scan filter dropdown."""
    return await hibid.fetch_categories()


@router.post("/scan", response_model=List[schemas.AuctionOut])
async def scan_auctions(payload: schemas.ScanRequest,
                        db: Session = Depends(get_db)):
    """Discover open auctions near the configured zip and store them."""
    # Keep the table from growing without bound — every scan appends.
    removed = purge_stale_auctions(db)
    if removed:
        print(f"Purged {removed} closed auctions with no imported lots")

    # A nationwide ("Anywhere") scan with no status limit would return every
    # open auction on HiBid — enforced here too, not just in the UI, so it
    # can't be bypassed by calling this endpoint directly.
    status = payload.status
    if payload.radius_miles == -1 and status not in ("CLOSING", "HOT"):
        status = "CLOSING"

    job = jobs.start("scan", "Scanning HiBid for auctions…")
    try:
        found = await hibid.discover_auctions(
            zip_code=payload.zip,
            radius_miles=payload.radius_miles,
            closing_within_days=payload.closing_within_days,
            include_nationwide=payload.include_nationwide,
            search_text=payload.search_text,
            category_id=payload.category_id,
            auction_type=payload.auction_type,
            status=status,
        )
    finally:
        jobs.finish(job)
    # Forgotten sales never make it into the table, so no later purge or
    # re-scan can resurrect them and nothing downstream has to re-filter.
    forgotten = dismissed.ids(db)
    skipped = sum(1 for a in found if a.get("hibid_id") in forgotten)
    if skipped:
        print(f"Skipped {skipped} forgotten auctions")
    stored = []
    for a in found:
        if a.get("hibid_id") in forgotten:
            continue
        row = db.query(models.Auction).filter(
            models.Auction.hibid_id == a["hibid_id"]).first()
        if row:
            for k, v in a.items():
                setattr(row, k, v)
        else:
            row = models.Auction(**a)
            db.add(row)
        stored.append(row)
    db.commit()

    # When scanning within a category and/or a keyword, annotate each
    # auction with how many of its lots actually match — so the UI can
    # offer "import just those" instead of the whole catalog.
    search_text = (payload.search_text or "").strip()
    if (payload.category_id and payload.category_id != -1) or search_text:
        cjob = jobs.start("scan", f"Counting matching lots in {len(stored)} auctions…",
                          total=len(stored))
        try:
            counts = await hibid.count_matching_lots(
                [r.hibid_id for r in stored if r.hibid_id], payload.category_id,
                search_text)
        finally:
            jobs.finish(cjob)
        for r in stored:
            r.category_lot_count = counts.get(r.hibid_id)
            r.category_count_for = payload.category_id
            r.category_count_search = search_text or None
        db.commit()   # persist so a page refresh keeps the "Import N" button
    # Auto-analyze shipping for auctions the user can't drive to: anything a
    # non-local scan surfaced would have to ship, so its terms are worth an
    # AI read up front. ship_analyzed_at gates it — an auction is only ever
    # paid for once, no matter how many scans re-surface it.
    to_analyze = [r.id for r in stored
                  if r.source == "Ship"
                  and (r.ship_analyzed_at is None
                       # A Canadian house whose terms never settled the
                       # border question is asked again - cents per scan,
                       # against lots that could never arrive.
                       or (shipping.is_canadian(r.state) and r.ships_to_us is None))]
    if to_analyze:
        jobs.enqueue("ship-analysis", "Reading shipping policies",
                     total=len(to_analyze), payload={"auction_ids": to_analyze})

    # Attach the same stats GET /auctions serves — without this, previously
    # imported auctions come back with zeroed counts and the UI shows them
    # as "Not imported yet" until the next idle refresh.
    return _attach_stats(db, stored)


@router.post("/{auction_id}/import", status_code=202)
def import_lots(auction_id: int, category_id: int = -1,
                bolo_only: bool = False, search_text: str = "",
                db: Session = Depends(get_db)):
    """Queue one auction's lot import on the worker (idempotent upsert).
    category_id and/or search_text limit the import to a HiBid category
    and/or a keyword, applied server-side by the same lot search the scan
    used to count matches.

    This used to fetch and save inline, holding the HTTP request open while
    HiBid paged through the catalog — fine at 80 lots, a timeout at 1,200,
    and closing the tab cancelled the request coroutine and the import with
    it. The worker's bulk path is resumable, survives deploys, and fetches
    the same premium metadata; a single auction is simply a bulk of one.
    """
    auction = db.query(models.Auction).filter(models.Auction.id == auction_id).first()
    if not auction or not auction.hibid_id:
        raise HTTPException(status_code=404, detail="Auction not found")
    # No refusal here: this queues behind whatever import-all is already
    # running (the worker serialises heavy jobs itself, in order) instead
    # of making the user re-click once the first one finishes.
    jobs.enqueue("import-all", f"Importing lots from {auction.name}",
                 total=1,
                 payload={"auction_ids": [auction_id],
                          "category_id": category_id,
                          "bolo_only": bolo_only,
                          "search_text": search_text})
    return {"auction_id": auction_id, "queued": True}


@router.post("/import-all", status_code=202)
def import_all(payload: schemas.ImportAllRequest, db: Session = Depends(get_db)):
    """Import lots from MANY auctions as one background job — the "scan found
    20 auctions with a few antiques each" case. category_id and/or
    search_text limit every import to a HiBid category and/or a keyword —
    the same filters the scan counted matches with. The caller sends the
    auctions in display order and leaves out ones it knows have no
    matching lots."""
    known = {a.id for a in
             db.query(models.Auction.id)
               .filter(models.Auction.id.in_(payload.auction_ids),
                       models.Auction.hibid_id.isnot(None))
               .all()}
    ids = [aid for aid in payload.auction_ids if aid in known]
    if not ids:
        return {"auctions": 0, "queued": False}
    jobs.enqueue("import-all", f"Importing lots from {len(ids)} auctions",
                 total=len(ids),
                 payload={"auction_ids": ids,
                          "category_id": payload.category_id,
                          "bolo_only": payload.bolo_only,
                          "search_text": payload.search_text})
    return {"auctions": len(ids), "queued": True}


@router.post("/{auction_id}/hide", response_model=schemas.AuctionOut)
def set_auction_hidden(auction_id: int, hidden: bool = True,
                       db: Session = Depends(get_db)):
    """Forget an auction you're not interested in (or bring it back).

    Scans keep re-surfacing the same sales, so a judgement made once should
    stick — permanently. The dismissal is recorded against the HiBid event id
    (services/dismissed.py) as well as the row, because the row itself can be
    purged once the sale closes; without the durable record, a re-scan brought
    dismissed auctions back. Any lots already imported stay put.
    """
    auction = (db.query(models.Auction)
                 .filter(models.Auction.id == auction_id).first())
    if not auction:
        raise HTTPException(status_code=404, detail="Auction not found")
    auction.hidden = hidden
    db.commit()
    if auction.hibid_id:
        if hidden:
            dismissed.add(db, auction.hibid_id, auction.name)
        else:
            dismissed.remove(db, auction.hibid_id)
    db.refresh(auction)
    return _attach_stats(db, [auction])[0]


@router.get("/dismissed")
def list_dismissed(db: Session = Depends(get_db)):
    """Auctions the user has forgotten, newest first — powers the restore
    list, which is the only way back once a sale is skipped at scan time."""
    return [{"hibid_id": r.hibid_id, "name": r.name, "created_at": r.created_at}
            for r in dismissed.listed(db)]


@router.delete("/dismissed/{hibid_id}")
def undismiss(hibid_id: int, db: Session = Depends(get_db)):
    """Un-forget one auction. It reappears on the next scan that finds it;
    any row still on file is un-hidden immediately."""
    restored = dismissed.remove(db, hibid_id)
    (db.query(models.Auction)
       .filter(models.Auction.hibid_id == hibid_id)
       .update({"hidden": False}, synchronize_session=False))
    db.commit()
    return {"restored": restored}


@router.post("/{auction_id}/enrich-all", status_code=202)
def enrich_all(auction_id: int, skip_hard: bool = False, db: Session = Depends(get_db)):
    """Queue enrichment for every pending/failed lot in an auction. Safe to
    re-run — already-successful lots are skipped. skip_hard leaves out
    HARD-to-ship lots, which rarely clear the ROI bar and cost the same to
    enrich as anything else."""
    from .enrichment import _worth_pricing
    q = (db.query(models.Lot).join(models.Enrichment)
           .filter(models.Lot.auction_id == auction_id,
                   _worth_pricing(),
                   models.Enrichment.status.in_(["pending", "failed"])))
    if skip_hard:
        q = q.filter(models.Lot.logistics_ease != "HARD")
    lot_ids = [lot.id for lot in q.all()]
    if not lot_ids:
        return {"auction_id": auction_id, "queued": 0}
    db.query(models.Enrichment).filter(
        models.Enrichment.lot_id.in_(lot_ids)
    ).update({"status": "queued", "queued_task": "enrich",
              "queued_at": datetime.now(timezone.utc),
              "queue_rank": 0, "claimed_at": None},
             synchronize_session=False)
    db.commit()
    return {"auction_id": auction_id, "queued": len(lot_ids)}
