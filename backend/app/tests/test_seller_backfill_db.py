"""Filling in seller history for lots already here (`pytest -m db`).

The gate only acts on facts a scan fetched. A lot imported before the gate
existed has none, and an unknown seller keeps the benefit of the doubt -
so a burner account's gold badge stands until somebody rescans that
keyword. This is that rescan without the scan: look the seller up, write
it onto their lots, re-grade on the spot.

Re-grading is pure arithmetic over stored values. Nothing here spends a
comp lookup or an AI call; only the badge can change.
"""

from datetime import datetime, timezone

import pytest

from app import models
from app.database import SessionLocal
from app.workers.sellers import apply_to_lots, seller_ids_on_file

pytestmark = pytest.mark.db

TEST_HIBID = 999999901
BURNER = "770000001"
REAL = "770000002"


def _purge(db):
    for a in db.query(models.Auction).filter(
            models.Auction.hibid_id == TEST_HIBID).all():
        ids = [r[0] for r in db.query(models.Lot.id)
                               .filter(models.Lot.auction_id == a.id).all()]
        if ids:
            db.query(models.Enrichment).filter(
                models.Enrichment.lot_id.in_(ids)).delete(synchronize_session=False)
        db.query(models.Lot).filter(models.Lot.auction_id == a.id).delete(
            synchronize_session=False)
        db.query(models.Auction).filter(models.Auction.id == a.id).delete(
            synchronize_session=False)
    db.commit()


@pytest.fixture
def seeded():
    scrub = SessionLocal()
    _purge(scrub)
    scrub.close()
    db = SessionLocal()
    a = models.Auction(hibid_id=TEST_HIBID, name="seller-backfill-test",
                       source="Ship", buyer_premium_mult=1.05,
                       imported_at=datetime.now(timezone.utc))
    db.add(a)
    db.flush()
    for i, sid in ((1, BURNER), (2, BURNER), (3, REAL)):
        lot = models.Lot(lot_id=f"sb-{i}", title="Nintendo Switch OLED",
                         auction_id=a.id, current_bid=40, next_bid=41,
                         status="OPEN", logistics_ease="EASY", source="Ship",
                         seller_id=sid)
        db.add(lot)
        db.flush()
        db.add(models.Enrichment(lot_id=lot.id, status="success", est_resale=300,
                                 comp_count=8, price_source="sold (SoldComps)",
                                 roi_status="GOLD MINE", user_overrides=[]))
    db.commit()
    yield db, a
    db.rollback()
    _purge(db)
    db.close()


def _grades(db, a):
    db.expire_all()
    return {l.lot_id: (l.enrichment.roi_status, l.enrichment.roi_reason)
            for l in db.query(models.Lot).filter(models.Lot.auction_id == a.id)}


def test_a_burners_lots_lose_the_badge(seeded):
    db, a = seeded
    n = apply_to_lots(db, BURNER, {"rating": None, "feedback_count": None,
                                   "item_count": 1, "bought_count": 0})
    db.commit()
    assert n == 2
    g = _grades(db, a)
    assert g["sb-1"][0] == "PASS"
    assert "no ratings" in g["sb-1"][1]
    assert g["sb-2"][0] == "PASS"
    assert g["sb-3"][0] == "GOLD MINE"          # the other seller is untouched


def test_a_real_sellers_lots_keep_it(seeded):
    db, a = seeded
    apply_to_lots(db, REAL, {"rating": 5.0, "feedback_count": 27,
                             "item_count": 51, "bought_count": 26})
    db.commit()
    assert _grades(db, a)["sb-3"][0] == "GOLD MINE"


def test_a_lookup_that_told_us_nothing_writes_nothing(seeded):
    """All-None would be stored as "no history", which is exactly the state
    the backfill is trying to leave. Better to stay unknown."""
    db, a = seeded
    n = apply_to_lots(db, BURNER, {"rating": None, "feedback_count": None,
                                   "item_count": None, "bought_count": None})
    db.commit()
    assert n == 0
    db.expire_all()
    lot = db.query(models.Lot).filter(models.Lot.lot_id == "sb-1").one()
    assert lot.seller_item_count is None
    assert _grades(db, a)["sb-1"][0] == "GOLD MINE"


def test_the_value_survives_the_regrade(seeded):
    db, a = seeded
    apply_to_lots(db, BURNER, {"rating": None, "feedback_count": None,
                               "item_count": 1, "bought_count": 0})
    db.commit()
    db.expire_all()
    e = db.query(models.Lot).filter(models.Lot.lot_id == "sb-1").one().enrichment
    assert float(e.est_resale) == 300

def test_it_finds_the_sellers_with_lots_on_file(seeded):
    db, a = seeded
    ids = seller_ids_on_file(db)
    assert BURNER in ids and REAL in ids
    assert len(ids) == len(set(ids))            # one entry per seller
