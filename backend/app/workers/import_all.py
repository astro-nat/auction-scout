"""Bulk import — pull lots for MANY auctions in one background job.

A category scan that surfaces 20 auctions with a few antiques each used to
mean 20 Import clicks, each a separate request-scoped import. This is the
same import run per auction by the worker process, following the
persisted-job pattern (see workers/enrich.py): the id list and category ride
the jobs row, `current` is the resume checkpoint, and the reaper restarts it
after a crash. Single-auction Import stays request-scoped in
routers/auctions.py; both share save_lots() below.
"""

import asyncio
import logging
from contextlib import contextmanager
from datetime import datetime, timezone

import httpx
from sqlalchemy.orm import Session

from ..database import SessionLocal
from .. import models
from ..services import hibid, jobs
from ..services import shipping
from ..services.timing import timed
from .enrich import _apply_roi, apply_bolo_match, looks_multi_item
from ..services.bolo import category_hint

logger = logging.getLogger(__name__)



@contextmanager
def _step(phase, name: str):
    """Time an inner step, or do nothing when the caller passed no phase.

    save_lots is called from tests and from paths that do not record, so
    the instrumentation has to be free when it is switched off.
    """
    if phase is None:
        yield
        return
    with phase.sub(name):
        yield


def save_lots(db: Session, auction: models.Auction, lots: list[dict], *,
              on_progress=None, should_cancel=None,
              bolo_only: bool = False, phase=None) -> tuple[int, int, bool]:
    """Idempotent upsert of fetched lot dicts into one auction.

    Bids/status/time-left always come fresh on rows we already have;
    analysis fields stay. Returns (created, updated, cancelled).

    Batched, because this used to cost 227ms per lot in production against
    2ms locally. The difference was entirely round trips: a SELECT per lot
    to see whether it existed, then a flush per new lot to get its id back.
    Against a database one hop away that is invisible; across a network it
    is 110x and a 1,182-lot auction spent four and a half minutes here.

    Now: one SELECT per chunk of ids, then one flush for all new lots and
    one for their enrichment rows. Three statements instead of a few
    thousand.
    """
    created = updated = skipped = 0
    cancelled = False
    total = len(lots)

    # --- 1. which of these do we already have? one query, not one per lot
    incoming = [d for d in lots if d.get("lot_id")]
    existing: dict = {}
    with _step(phase, "lookup_existing"):
        ids = [d["lot_id"] for d in incoming]
        # Chunked: a 3,000-element IN list is fine, a 30,000 one is not.
        for i in range(0, len(ids), 1000):
            for row in (db.query(models.Lot)
                          .filter(models.Lot.lot_id.in_(ids[i:i + 1000])).all()):
                existing[row.lot_id] = row

    # --- 2. decide, in memory, what to update and what to create
    FRESH = ("current_bid", "next_bid", "bid_count", "est_cost",
             "status", "time_left", "closes_at", "lot_number",
             "estimate_low", "estimate_high",
             "thumbnail_url", "hd_thumbnail_url", "fullsize_url")
    new_rows: list = []
    new_data: list = []
    for i, data in enumerate(incoming, 1):
        if i % 200 == 0 or i == total:
            if should_cancel and should_cancel():
                cancelled = True
                break            # keep what's decided so far
            if on_progress:
                on_progress(i, (data.get("title") or "")[:45])

        row = existing.get(data["lot_id"])
        if row is not None:
            # Already on file: always refreshed, filter or not. Dropping a
            # lot's live bids because the filter changed would lose real
            # data over a display choice.
            bid_before = row.current_bid
            for k in FRESH:
                setattr(row, k, data[k])
            # Photos are added, never cleared. A lot already on file when the
            # photo list started being kept had no way to gain one: FRESH
            # cannot hold image_urls, because a source that does not report
            # photos would then wipe what another pass fetched (a
            # PublicSurplus detail is fetched by its own router). So copy
            # them across only when this import actually carries them, which
            # is what lets a re-import backfill the lots imported earlier.
            for k in ("image_urls", "image_count"):
                if data.get(k):
                    setattr(row, k, data[k])
            # The bid moved, so the verdict computed against the old one is
            # no longer true. refresh.py has always done this; import never
            # did, which left lots badged GOLD MINE at a bid several times
            # their own max - one sat at $37 against a $10.58 ceiling.
            # Pure arithmetic over stored values: no comps, no AI, no network.
            if row.current_bid != bid_before and row.enrichment is not None:
                _apply_roi(row, row.enrichment)
            updated += 1
            continue

        if bolo_only:
            # Free regex over the title the fetch already returned - no AI,
            # no network - which is what lets the filter decide before
            # anything is spent.
            probe = models.Enrichment(lot_id=0)
            with _step(phase, "bolo_match"):
                hit = apply_bolo_match(
                    probe, data.get("title") or "", data.get("description") or "")
            if not hit:
                # A box lot rarely names a brand. "Lot of Assorted Cameras"
                # is exactly what to open when cameras are on the list.
                title_text = data.get("title") or ""
                hit = bool(looks_multi_item(title_text)
                           and category_hint(title_text))
            if not hit:
                skipped += 1
                continue

        new_rows.append(models.Lot(auction_id=auction.id, **data))
        new_data.append(data)

    # --- 3. one flush for every new lot, then one for their enrichments
    if new_rows:
        with _step(phase, "insert_lots"):
            db.add_all(new_rows)
            db.flush()          # populates row.id for the FK below
        with _step(phase, "insert_enrichments"):
            enrichments = []
            for row, data in zip(new_rows, new_data):
                e = models.Enrichment(lot_id=row.id, status="pending")
                # Recorded now so enrichment does not redo it, and so the
                # items view can filter by brand before any AI has run.
                apply_bolo_match(e, data.get("title") or "",
                                 data.get("description") or "")
                enrichments.append(e)
            db.add_all(enrichments)
            db.flush()
        created = len(new_rows)

    auction.imported_at = datetime.now(timezone.utc)
    with _step(phase, "final_commit"):
        db.commit()
    if skipped:
        logger.info("Import (BOLO only): kept %d, skipped %d non-matching lots",
                    created, skipped)
    return created, updated, cancelled


def run_import_all(auction_ids: list[int], resume_job_id: str | None = None,
                   category_id: int = -1, bolo_only: bool = False,
                   search_text: str = "") -> None:
    """Import every auction in the list, one at a time.

    category_id limits each import to one HiBid category, search_text to a
    keyword (the "import all the pyrex" case) — both applied server-side by
    the same HiBid lot search the scan used to count matches, and they
    compose. They ride the job payload rather than an argument the
    dispatcher would have to know about, so a resume keeps them too.
    """
    db: Session = SessionLocal()
    if resume_job_id:
        job = resume_job_id
        row = jobs.get(job) or {}
        start_at = row.get("current") or 0
        category_id = (row.get("payload") or {}).get("category_id", category_id)
        # Same reasoning as category_id: the worker dispatches with only the
        # auction list and the job id, so anything else has to ride the
        # payload or it silently resets to its default on every run.
        bolo_only = (row.get("payload") or {}).get("bolo_only", bolo_only)
        search_text = (row.get("payload") or {}).get("search_text", search_text)
    else:
        label = (f"Importing BOLO matches from {len(auction_ids)} auctions" if bolo_only
                 else f"Importing '{search_text}' lots from {len(auction_ids)} auctions"
                 if search_text else
                 f"Importing lots from {len(auction_ids)} auctions")
        job = jobs.start("import-all", label,
                         total=len(auction_ids),
                         payload={"auction_ids": auction_ids,
                                  "category_id": category_id,
                                  "bolo_only": bolo_only,
                                  "search_text": search_text})
        start_at = 0
    imported = created_total = updated_total = 0
    try:
        remaining = auction_ids[start_at:]
        for i, auction_id in enumerate(remaining, start_at + 1):
            if jobs.is_cancelled(job):
                print(f"Import-all cancelled after {i - 1} auctions")
                break
            auction = (db.query(models.Auction)
                         .filter(models.Auction.id == auction_id).first())
            if not auction or not auction.hibid_id:
                jobs.update(job, current=i)
                continue
            name = (auction.name or "")[:40]
            jobs.update(job, current=i, label=f"Importing {name}")
            try:
                async def _fetch():
                    async with httpx.AsyncClient() as client:
                        meta = await hibid.fetch_auction_meta(client, [auction.hibid_id])
                    m = meta.get(auction.hibid_id, {})
                    if m.get("premium_mult"):
                        auction.buyer_premium_mult = m["premium_mult"]
                        auction.cond_ship = m.get("cond_ship", False)
                    # A Canadian house's plainly worded border policy is
                    # free to read here; the AI pass covers the rest.
                    if auction.ships_to_us is None and shipping.is_canadian(auction.state):
                        auction.ships_to_us = shipping.ships_to_us_from_text(
                            m.get("ship_text", ""), m.get("terms_text", ""))
                    ctx = {"premium_mult": auction.buyer_premium_mult,
                           "source": auction.source}
                    return await hibid.fetch_lots(
                        auction.hibid_id, auction_ctx=ctx, category_id=category_id,
                        search_text=search_text,
                        # Detail isn't rendered, but update() heartbeats — so
                        # a slow 2,000-lot catalog can't look like a dead job.
                        on_progress=lambda fetched, total: jobs.update(
                            job, detail=f"{name} — {fetched}/{total} lots"),
                        should_cancel=lambda: jobs.is_cancelled(job))

                with timed("import", "fetch", job_id=job,
                           auction_id=auction_id, label=name) as ph:
                    lots = asyncio.run(_fetch())
                    ph.add(len(lots))
                    ph.note(category_id=category_id, bolo_only=bolo_only)
            except Exception as exc:  # noqa: BLE001 — one bad auction must not stop the run
                logger.warning("Import-all fetch failed for auction %s: %s",
                               auction_id, exc)
                continue
            with timed("import", "save", job_id=job, auction_id=auction_id,
                       label=name) as ph:
                # Report through the save as well as the fetch. Without
                # this the bar sat at "1182/1182" for four minutes while
                # the save ran, which reads as a hung job - and the job
                # heartbeat stopped too, so a slow save could be reaped
                # out from under itself.
                c, u, _ = save_lots(
                    db, auction, lots, bolo_only=bolo_only, phase=ph,
                    on_progress=lambda n, title: jobs.update(
                        job, detail=f"{name} - saving {n}/{len(lots)}"),
                    should_cancel=lambda: jobs.is_cancelled(job))
                ph.add(len(lots))
                ph.note(created=c, updated=u, fetched=len(lots),
                        bolo_only=bolo_only)
            created_total += c
            updated_total += u
            imported += 1
    finally:
        jobs.finish(job)
        db.close()
    print(f"Import-all complete: {imported} auctions, "
          f"{created_total} new lots, {updated_total} refreshed")
