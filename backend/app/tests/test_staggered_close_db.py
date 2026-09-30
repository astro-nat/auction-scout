"""A sale whose lots close one after another is not over at its first lot
(`pytest -m db`).

Reported: lots of PART 2 - Watermark Estate showing closed that were not.
Its closing_date is when closing STARTS (6 pm); the lots closed a minute or
two apart for hours after. Every check that read closing_date alone called
the whole sale over at 6 pm: 853 open lots flagged closed, the bid refresh
skipping it, pricing skipping it - and the automatic flush set to delete
the lots that were still open.
"""

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app import models
from app.database import SessionLocal
from app.main import app
from app.routers.lots import flush_closed_now
from app.services import open_state
from app.workers.refresh import auctions_due_for_bid_refresh

pytestmark = pytest.mark.db
client = TestClient(app)

HIBID = 999999851
OPEN_LOT, CLOSED_LOT = "stag-open", "stag-closed"


def _clean(db):
    for a in db.query(models.Auction).filter(models.Auction.hibid_id == HIBID).all():
        ids = [r[0] for r in db.query(models.Lot.id).filter(models.Lot.auction_id == a.id)]
        if ids:
            db.query(models.PriceObservation).filter(
                models.PriceObservation.lot_id.in_(ids)).delete(synchronize_session=False)
            db.query(models.Enrichment).filter(
                models.Enrichment.lot_id.in_(ids)).delete(synchronize_session=False)
            db.query(models.Lot).filter(models.Lot.id.in_(ids)).delete(synchronize_session=False)
        db.delete(a)
    db.commit()


@pytest.fixture
def sale():
    """Closing started an hour ago; one lot still has an hour to go, one
    closed two hours ago (past the flush's one-hour grace)."""
    db = SessionLocal()
    _clean(db)
    now = datetime.utcnow()
    a = models.Auction(hibid_id=HIBID, name="Staggered test sale", source="Local Pickup",
                       closing_date=now - timedelta(hours=1))
    db.add(a)
    db.flush()
    for lot_id, closes in ((OPEN_LOT, now + timedelta(hours=1)),
                           (CLOSED_LOT, now - timedelta(hours=2))):
        lot = models.Lot(lot_id=lot_id, auction_id=a.id, title=f"Test lot {lot_id}",
                         status="OPEN", closes_at=closes, current_bid=5)
        db.add(lot)
        db.flush()
        db.add(models.Enrichment(lot_id=lot.id, status="pending"))
    db.commit()
    yield db, a
    _clean(db)
    db.close()


def test_the_sale_is_still_open_while_a_lot_is(sale):
    db, a = sale
    now = datetime.utcnow()
    assert db.query(models.Auction).filter(models.Auction.id == a.id,
                                           open_state.still_open(now)).count() == 1
    assert db.query(models.Auction).filter(models.Auction.id == a.id,
                                           open_state.is_over(now)).count() == 0


def test_each_lot_is_closed_by_its_own_time(sale):
    db, a = sale
    r = client.get("/lots", params={"auction_id": a.id})
    assert r.status_code == 200, r.text
    closed = {l["lot_id"]: l["auction_closed"] for l in r.json()}
    assert closed == {OPEN_LOT: False, CLOSED_LOT: True}


def test_the_flush_takes_the_closed_lot_and_leaves_the_open_one(sale):
    db, a = sale
    flush_closed_now(db)
    left = {r[0] for r in db.query(models.Lot.lot_id).filter(models.Lot.auction_id == a.id)}
    assert left == {OPEN_LOT}, "deleted a lot that was still open"


def test_bids_keep_refreshing_while_lots_are_open(sale):
    db, a = sale
    assert a.id in auctions_due_for_bid_refresh(db, window_hours=0)
