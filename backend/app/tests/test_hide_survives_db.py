"""A hidden lot must stay hidden (`pytest -m db`).

Reported: "I hide items and they come back." The hide itself persists -
the route commits and reads back - but hiding is stored on the lot ROW,
and the row does not survive the round trip:

    hide it  ->  it closes  ->  flush deletes it  ->  re-import recreates
    it from the catalogue, with hidden defaulting to false.

Flush spares watched lots and nothing else, and since lots now flush an
hour after their own closing time, this happens to anything hidden in a
sale that is still being re-imported. On the inventory it showed up as 27
hide presses in 48 hours leaving the hidden count unchanged at 28 - and
every one of those 28 is a PublicSurplus lot that does not close until
October.

Dismissed AUCTIONS already solve this: services/dismissed.py remembers the
decision by HiBid id, so it survives the row.
"""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import models
from app.main import app
from app.database import SessionLocal
from app.routers.lots import flush_closed_now
from app.workers.import_all import save_lots

pytestmark = pytest.mark.db

client = TestClient(app)
TEST_HIBID = 999999881


def _hide_it(lot_id="hs-1"):
    """Hide the way the app does - through the route, so whatever the route
    records gets recorded. Setting the column by hand would test a path
    nobody uses."""
    r = client.post(f"/lots/{lot_id}/hide", params={"hidden": True})
    assert r.status_code == 200, r.text


def _incoming(lot_id="hs-1", bid=5):
    return {"lot_id": lot_id, "title": "Vintage Canon F-1 35mm Film Camera",
            "category": "General", "description": "", "current_bid": bid,
            "next_bid": bid + 1, "bid_count": 1, "est_cost": 6, "status": "OPEN",
            "time_left": "2d", "closes_at": None, "lot_number": "641",
            "estimate_low": None, "estimate_high": None, "source": "Ship",
            "logistics_ease": "EASY", "unreachable_pickup": False,
            "lot_link": None, "thumbnail_url": None, "hd_thumbnail_url": None,
            "fullsize_url": None, "image_count": 0}


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
def auction():
    scrub = SessionLocal()
    _purge(scrub)
    scrub.close()
    db = SessionLocal()
    a = models.Auction(hibid_id=TEST_HIBID, name="hide-survives-test",
                       source="Ship", buyer_premium_mult=1.15,
                       closing_date=datetime.now() + timedelta(days=5),
                       imported_at=datetime.now(timezone.utc))
    db.add(a)
    db.commit()
    yield db, a
    db.rollback()
    _purge(db)
    db.close()


def _hidden(db, lot_id="hs-1"):
    db.expire_all()
    row = db.query(models.Lot).filter(models.Lot.lot_id == lot_id).first()
    return None if row is None else bool(row.hidden)


def test_a_hidden_lot_survives_the_flush_and_a_re_import(auction):
    """The whole round trip, as it happens in use."""
    db, a = auction
    save_lots(db, a, [_incoming()])
    db.commit()
    _hide_it()
    assert _hidden(db) is True

    # Its own closing time passes, so the flush takes it.
    lot = db.query(models.Lot).filter(models.Lot.lot_id == "hs-1").one()
    lot.closes_at = datetime.now() - timedelta(hours=3)
    db.commit()
    flush_closed_now(db)

    # The sale is still live, so the next import brings the lot back.
    save_lots(db, a, [_incoming()])
    assert _hidden(db) is True, "the lot came back unhidden"


def test_hiding_is_remembered_even_if_the_row_goes(auction):
    """The narrow version: the decision must outlive the row."""
    db, a = auction
    save_lots(db, a, [_incoming()])
    db.commit()
    _hide_it()

    ids = [r[0] for r in db.query(models.Lot.id)
                           .filter(models.Lot.lot_id == "hs-1").all()]
    db.query(models.Enrichment).filter(
        models.Enrichment.lot_id.in_(ids)).delete(synchronize_session=False)
    db.query(models.Lot).filter(models.Lot.lot_id == "hs-1").delete(
        synchronize_session=False)
    db.commit()
    assert _hidden(db) is None

    save_lots(db, a, [_incoming()])
    assert _hidden(db) is True, "a re-imported lot forgot it was hidden"


def test_an_ordinary_re_import_does_not_hide_things(auction):
    """The memory must not leak onto lots nobody hid."""
    db, a = auction
    save_lots(db, a, [_incoming("hs-2")])
    assert _hidden(db, "hs-2") is False
    save_lots(db, a, [_incoming("hs-2")])
    assert _hidden(db, "hs-2") is False
