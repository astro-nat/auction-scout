"""BOLO-filtered import (`pytest -m db`).

The point of doing the match at IMPORT rather than at enrichment is that it
is free: regex over the title and description the fetch already returned, no
AI call, no network. That is what makes it possible to decide what to keep
before anything is spent.

Two behaviours matter beyond "it filters":
  - a lot already on file is updated regardless of the filter, because
    dropping its live bids over a filter change would be worse
  - the BOLO fields are written at import, so the items view can filter on
    them before enrichment has run at all
"""

from datetime import datetime, timezone

import pytest

from app import models
from app.database import SessionLocal
from app.workers.import_all import save_lots

pytestmark = pytest.mark.db

TEST_HIBID = 999999970


def _lot(lot_id, title):
    """The shape hibid.fetch_lots returns, trimmed to what save_lots uses."""
    return {
        "lot_id": lot_id, "title": title, "category": "General",
        "description": "", "current_bid": 1, "next_bid": 2, "bid_count": 0,
        "est_cost": 1.15, "status": "OPEN", "time_left": "2d", "closes_at": None,
        "lot_number": None, "estimate_low": None, "estimate_high": None,
        "source": "Ship", "logistics_ease": "EASY", "unreachable_pickup": False,
        "lot_link": None, "thumbnail_url": None, "hd_thumbnail_url": None,
        "fullsize_url": None, "image_count": 0,
    }


FETCHED = [
    _lot("bi-1", "McIntosh MC275 Tube Amplifier"),        # BOLO
    _lot("bi-2", "Assorted Plastic Storage Totes Lot"),   # not
    _lot("bi-3", "Makita XPH12Z Hammer Drill"),           # BOLO
    _lot("bi-4", "Box of Used Coat Hangers"),             # not
    _lot("bi-5", "Griswold No 8 Cast Iron Skillet"),      # BOLO
]


@pytest.fixture
def auction():
    db = SessionLocal()
    a = models.Auction(hibid_id=TEST_HIBID, name="bolo-import-test",
                       imported_at=datetime.now(timezone.utc))
    db.add(a)
    db.commit()
    yield db, a
    lot_ids = [r[0] for r in db.query(models.Lot.id)
                              .filter(models.Lot.auction_id == a.id).all()]
    if lot_ids:
        db.query(models.Enrichment).filter(
            models.Enrichment.lot_id.in_(lot_ids)).delete(synchronize_session=False)
    db.query(models.Lot).filter(models.Lot.auction_id == a.id).delete(
        synchronize_session=False)
    db.query(models.Auction).filter(models.Auction.id == a.id).delete(
        synchronize_session=False)
    db.commit()
    db.close()


def _titles(db, a):
    return sorted(r[0] for r in db.query(models.Lot.title)
                                  .filter(models.Lot.auction_id == a.id).all())


def test_importing_everything_keeps_everything(auction):
    db, a = auction
    created, _, _ = save_lots(db, a, FETCHED)
    assert created == 5
    assert len(_titles(db, a)) == 5


def test_bolo_only_keeps_just_the_matches(auction):
    db, a = auction
    created, _, _ = save_lots(db, a, FETCHED, bolo_only=True)
    assert created == 3, "expected the three brand matches"
    kept = _titles(db, a)
    assert "Box of Used Coat Hangers" not in kept
    assert "Assorted Plastic Storage Totes Lot" not in kept


def test_the_match_is_recorded_at_import_not_deferred(auction):
    """This is what lets the items view filter on BOLO before any AI has
    run, and it stops enrichment paying to work it out again."""
    db, a = auction
    save_lots(db, a, FETCHED, bolo_only=True)
    rows = (db.query(models.Lot.title, models.Enrichment.bolo_brand,
                     models.Enrichment.status)
              .join(models.Enrichment, models.Enrichment.lot_id == models.Lot.id)
              .filter(models.Lot.auction_id == a.id).all())
    assert rows, "no enrichment rows created"
    for title, brand, status in rows:
        assert brand, f"{title!r} imported under a BOLO filter with no brand"
        assert status == "pending", "import must not mark anything enriched"


def test_an_unfiltered_import_also_records_the_match(auction):
    """Free either way, so there is no reason to skip it."""
    db, a = auction
    save_lots(db, a, FETCHED)
    brand = (db.query(models.Enrichment.bolo_brand)
               .join(models.Lot, models.Lot.id == models.Enrichment.lot_id)
               .filter(models.Lot.title == "Makita XPH12Z Hammer Drill").scalar())
    assert brand == "Premium hand and power tools"


def test_existing_lots_are_updated_even_when_they_fail_the_filter(auction):
    """A lot already on file keeps getting fresh bids. Dropping those
    because the filter changed would lose live data over a display choice."""
    db, a = auction
    save_lots(db, a, FETCHED)                      # everything, first pass
    moved = [dict(d) for d in FETCHED]
    for d in moved:
        d["current_bid"] = 99
    created, updated, _ = save_lots(db, a, moved, bolo_only=True)
    assert created == 0
    assert updated == 5, "a non-matching lot already on file went stale"
    bids = {r[0] for r in db.query(models.Lot.current_bid)
                            .filter(models.Lot.auction_id == a.id).all()}
    assert bids == {99}


def test_nothing_matching_imports_nothing(auction):
    db, a = auction
    created, _, _ = save_lots(db, a, [_lot("bi-9", "Box of Used Coat Hangers")],
                              bolo_only=True)
    assert created == 0
