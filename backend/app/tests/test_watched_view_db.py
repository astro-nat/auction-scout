"""Watching a lot moves it out of the list it came from (`pytest -m db`).

Asked for as: "when I watch an item, add it to a watched items tab and take
it out of the priced inventory so it's easier to see new items." Watching
is a decision already made about a lot, so what is left in the inventory is
what still needs one.

The watched list is built server-side rather than filtered in the browser,
because a lot can be watched before anything has priced it - and a
priced-only page would never contain it to filter.
"""

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app import models
from app.database import SessionLocal
from app.main import app

pytestmark = pytest.mark.db

client = TestClient(app)
TEST_HIBID = 999999861


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
def sale():
    scrub = SessionLocal()
    _purge(scrub)
    scrub.close()
    db = SessionLocal()
    a = models.Auction(hibid_id=TEST_HIBID, name="watched-view-test",
                       source="Ship", buyer_premium_mult=1.15,
                       imported_at=datetime.now(timezone.utc))
    db.add(a)
    db.flush()
    for lid, resale in (("wv-priced", 120), ("wv-unpriced", None)):
        lot = models.Lot(lot_id=lid, title=f"Widget {lid}", auction_id=a.id,
                         current_bid=5, next_bid=6, status="OPEN",
                         logistics_ease="EASY", source="Ship")
        db.add(lot)
        db.flush()
        db.add(models.Enrichment(lot_id=lot.id, status="success",
                                 est_resale=resale, user_overrides=[]))
    db.commit()
    yield db, a
    db.rollback()
    _purge(db)
    db.close()


def _ids(**params):
    r = client.get("/lots", params={"limit": 2000, **params})
    assert r.status_code == 200, r.text
    body = r.json()
    lots = body.get("items", body) if isinstance(body, dict) else body
    return {l["lot_id"] for l in lots}


def _count(**params):
    return client.get("/lots/count", params=params).json()["total"]


def test_nothing_is_watched_to_begin_with(sale):
    got = _ids(watched_only=True)
    assert "wv-priced" not in got and "wv-unpriced" not in got


def test_a_watched_lot_appears_in_the_watched_list(sale):
    client.post("/lots/wv-priced/watch", params={"watched": True})
    assert "wv-priced" in _ids(watched_only=True)


def test_an_unpriced_lot_can_be_watched_too(sale):
    """The reason this is a server filter: a priced-only page would never
    hold this lot, so filtering one in the browser could not find it."""
    client.post("/lots/wv-unpriced/watch", params={"watched": True})
    got = _ids(watched_only=True)
    assert "wv-unpriced" in got
    assert "wv-unpriced" not in _ids(priced_only=True)


def test_the_count_follows(sale):
    before = _count(watched_only=True)
    client.post("/lots/wv-priced/watch", params={"watched": True})
    assert _count(watched_only=True) == before + 1
    client.post("/lots/wv-priced/watch", params={"watched": False})
    assert _count(watched_only=True) == before


def test_unwatching_takes_it_back_out(sale):
    client.post("/lots/wv-priced/watch", params={"watched": True})
    client.post("/lots/wv-priced/watch", params={"watched": False})
    assert "wv-priced" not in _ids(watched_only=True)


def test_watching_does_not_hide_or_delete_anything(sale):
    """It moves between views; it is not a hide and not a flush."""
    client.post("/lots/wv-priced/watch", params={"watched": True})
    assert "wv-priced" in _ids()            # still in All Inventory
    db, a = sale
    db.expire_all()
    lot = db.query(models.Lot).filter(models.Lot.lot_id == "wv-priced").one()
    assert lot.hidden in (False, None)
