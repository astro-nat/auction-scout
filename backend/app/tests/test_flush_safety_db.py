"""What the flush must never take, and what must outlive it (`pytest -m db`).

flush_closed_now deletes lots permanently - the enrichment goes with them,
including AI calls that were paid for. Two promises govern it, and only one
of them was written down anywhere:

  * "Watched lots are never flushed" - stated in the docstring, asserted
    nowhere. A watched lot is one the user asked to be alerted about; losing
    it silently is the worst outcome the flush can produce.

  * A hidden lot IS flushed, deliberately - it is junk, and keeping it
    forever defeats the flush. What must survive is the DECISION, so the
    next import does not hand it back. That is the bug behind "I hide items
    and they come back".
"""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import models
from app.database import SessionLocal
from app.main import app
from app.routers.lots import flush_closed_now
from app.workers.import_all import save_lots

pytestmark = pytest.mark.db

client = TestClient(app)
TEST_HIBID = 999999871


def _incoming(lot_id, title="Griswold No 8 Cast Iron Skillet", bid=5):
    return {"lot_id": lot_id, "title": title, "category": "General",
            "description": "", "current_bid": bid, "next_bid": bid + 1,
            "bid_count": 1, "est_cost": 6, "status": "OPEN", "time_left": "2d",
            "closes_at": None, "lot_number": "1", "estimate_low": None,
            "estimate_high": None, "source": "Ship", "logistics_ease": "EASY",
            "unreachable_pickup": False, "lot_link": None,
            "thumbnail_url": None, "hd_thumbnail_url": None,
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
    db.query(models.HiddenLot).filter(
        models.HiddenLot.lot_id.like("fs-%")).delete(synchronize_session=False)
    db.commit()


@pytest.fixture
def sale():
    scrub = SessionLocal()
    _purge(scrub)
    scrub.close()
    db = SessionLocal()
    a = models.Auction(hibid_id=TEST_HIBID, name="flush-safety-test",
                       source="Ship", buyer_premium_mult=1.15,
                       closing_date=datetime.now() + timedelta(days=5),
                       imported_at=datetime.now(timezone.utc))
    db.add(a)
    db.commit()
    yield db, a
    db.rollback()
    _purge(db)
    db.close()


def _close_it(db, lot_id):
    """Its own closing time passes, which is what the flush reads."""
    lot = db.query(models.Lot).filter(models.Lot.lot_id == lot_id).one()
    lot.closes_at = datetime.now() - timedelta(hours=3)
    db.commit()


def _exists(db, lot_id):
    db.expire_all()
    return db.query(models.Lot).filter(models.Lot.lot_id == lot_id).first() is not None


def _hidden(db, lot_id):
    db.expire_all()
    row = db.query(models.Lot).filter(models.Lot.lot_id == lot_id).first()
    return None if row is None else bool(row.hidden)


def test_a_watched_lot_is_never_flushed(sale):
    """The docstring has always promised this; now something checks it."""
    db, a = sale
    save_lots(db, a, [_incoming("fs-watch"), _incoming("fs-plain")])
    db.commit()
    assert client.post("/lots/fs-watch/watch", params={"watched": True}).status_code == 200
    _close_it(db, "fs-watch")
    _close_it(db, "fs-plain")

    flush_closed_now(db)
    assert _exists(db, "fs-watch"), "a watched lot was flushed"
    assert not _exists(db, "fs-plain"), "an ordinary closed lot should go"


def test_a_watched_lots_enrichment_goes_with_it_or_not_at_all(sale):
    """Deleting the lot but orphaning its enrichment would leave rows nothing
    can reach."""
    db, a = sale
    save_lots(db, a, [_incoming("fs-watch")])
    db.commit()
    client.post("/lots/fs-watch/watch", params={"watched": True})
    _close_it(db, "fs-watch")
    flush_closed_now(db)

    db.expire_all()
    lot = db.query(models.Lot).filter(models.Lot.lot_id == "fs-watch").one()
    assert lot.enrichment is not None


def test_a_hidden_lot_is_still_flushed(sale):
    """Hiding is not a reason to keep junk forever - the decision is what
    survives, not the row."""
    db, a = sale
    save_lots(db, a, [_incoming("fs-hide")])
    db.commit()
    client.post("/lots/fs-hide/hide", params={"hidden": True})
    _close_it(db, "fs-hide")

    flush_closed_now(db)
    assert not _exists(db, "fs-hide")
    assert db.query(models.HiddenLot).filter(
        models.HiddenLot.lot_id == "fs-hide").first() is not None


def test_a_bulk_hide_also_outlives_the_flush(sale):
    """hide-like hides several lots at once through a different code path,
    and it has to remember them the same way."""
    db, a = sale
    title = "Drieaz Humidifier ~ IA-25158"
    save_lots(db, a, [_incoming("fs-b1", title),
                      _incoming("fs-b2", "Drieaz Humidifier ~ IA-25157")])
    db.commit()
    r = client.post("/lots/fs-b1/hide-like")
    assert r.status_code == 200, r.text
    assert r.json()["changed"] >= 2

    for lid in ("fs-b1", "fs-b2"):
        _close_it(db, lid)
    flush_closed_now(db)
    save_lots(db, a, [_incoming("fs-b1", title),
                      _incoming("fs-b2", "Drieaz Humidifier ~ IA-25157")])
    db.commit()
    assert _hidden(db, "fs-b1") is True
    assert _hidden(db, "fs-b2") is True


def test_unhiding_is_remembered_too(sale):
    """Otherwise the memory would hand back a lot the user deliberately
    brought into view."""
    db, a = sale
    save_lots(db, a, [_incoming("fs-un")])
    db.commit()
    client.post("/lots/fs-un/hide", params={"hidden": True})
    client.post("/lots/fs-un/hide", params={"hidden": False})

    _close_it(db, "fs-un")
    flush_closed_now(db)
    save_lots(db, a, [_incoming("fs-un")])
    db.commit()
    assert _hidden(db, "fs-un") is False
