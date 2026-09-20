"""House-estimate calibration (`pytest -m db`): the flush captures each
closed lot's estimate-vs-hammer before deleting it, the median ratio comes
out right, and both API surfaces carry the result.

The regression this guards: house estimates anchored a real $84 loss
(estimate $250-500, hammer $100), and the evidence to calibrate them was
being deleted by the very flush that closes the books.
"""

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app import models
from app.routers.lots import flush_closed_now
from app.services import calibration
from app.main import app

pytestmark = pytest.mark.db

client = TestClient(app)

AUCTIONEER = 999888771
TEST_HIBID = 999999991
PREFIX = "pytest-calib-"


@pytest.fixture()
def clean():
    yield
    db = SessionLocal()
    db.query(models.EstimateObservation).filter(
        models.EstimateObservation.lot_id.like(f"{PREFIX}%")).delete(
        synchronize_session=False)
    ids = [r[0] for r in db.query(models.Lot.id)
                           .filter(models.Lot.lot_id.like(f"{PREFIX}%")).all()]
    db.query(models.Enrichment).filter(
        models.Enrichment.lot_id.in_(ids)).delete(synchronize_session=False)
    db.query(models.Lot).filter(models.Lot.id.in_(ids)).delete(synchronize_session=False)
    db.query(models.Auction).filter(
        models.Auction.hibid_id.between(TEST_HIBID, TEST_HIBID + 5)).delete(
        synchronize_session=False)
    db.commit()
    db.close()


def _closed_auction(db, offset=0):
    a = models.Auction(hibid_id=TEST_HIBID + offset,
                       name=f"PYTEST calib sale {offset}",
                       auctioneer="PYTEST House", auctioneer_id=AUCTIONEER,
                       closing_date=datetime.now() - timedelta(hours=2),
                       imported_at=datetime.now())
    db.add(a)
    db.flush()
    return a


def _lot(db, auction, suffix, *, low=None, high=None, bid=None):
    lot = models.Lot(lot_id=f"{PREFIX}{suffix}", auction_id=auction.id,
                     title=f"pytest {suffix}", status="SOLD",
                     estimate_low=low, estimate_high=high, current_bid=bid)
    db.add(lot)
    db.flush()
    db.add(models.Enrichment(lot_id=lot.id, status="success"))
    return lot


def _my_obs(db):
    return (db.query(models.EstimateObservation)
              .filter(models.EstimateObservation.lot_id.like(f"{PREFIX}%"))
              .all())


def test_flush_learns_before_it_deletes(clean):
    db = SessionLocal()
    a = _closed_auction(db)
    _lot(db, a, "sold", low=250, high=500, bid=100)      # the #261 shape
    _lot(db, a, "cheap", low=50, high=100, bid=40)
    _lot(db, a, "no-bids", low=80, high=120, bid=0)      # no hammer, no lesson
    _lot(db, a, "no-estimate", bid=30)                   # nothing to calibrate
    db.commit()
    db.close()

    flush_closed_now(SessionLocal())

    db = SessionLocal()
    obs = {o.lot_id: o for o in _my_obs(db)}
    lots_left = db.query(models.Lot).filter(
        models.Lot.lot_id.like(f"{PREFIX}%")).count()
    db.close()
    assert lots_left == 0                                # flush still flushes
    assert set(obs) == {f"{PREFIX}sold", f"{PREFIX}cheap"}
    o = obs[f"{PREFIX}sold"]
    assert float(o.estimate_low) == 250 and float(o.hammer) == 100
    assert o.auctioneer_id == AUCTIONEER


def test_recapture_is_idempotent(clean):
    db = SessionLocal()
    a = _closed_auction(db)
    lot = _lot(db, a, "dup", low=100, bid=50)
    db.commit()
    assert calibration.capture(db, [(lot, AUCTIONEER)]) == 1
    assert calibration.capture(db, [(lot, AUCTIONEER)]) == 0   # same lot again
    db.close()


def test_median_ratio_and_count(clean):
    db = SessionLocal()
    a = _closed_auction(db)
    # ratios: 5.0, 2.5, 2.0, 1.0 — median 2.25
    for i, (low, bid) in enumerate([(500, 100), (250, 100), (100, 50), (50, 50)]):
        calibration.capture(db, [(_lot(db, a, f"m{i}", low=low, bid=bid),
                                  AUCTIONEER)])
    db.commit()
    r = calibration.ratios(db)[AUCTIONEER]
    db.close()
    assert r == {"ratio": 2.25, "n": 4}


def test_ratio_rides_both_api_surfaces(clean):
    db = SessionLocal()
    a = _closed_auction(db)
    open_a = models.Auction(hibid_id=TEST_HIBID + 1, name="PYTEST calib open",
                            auctioneer="PYTEST House", auctioneer_id=AUCTIONEER,
                            closing_date=datetime.now() + timedelta(days=2),
                            imported_at=datetime.now())
    db.add(open_a)
    db.flush()
    live = _lot(db, open_a, "live", low=200, high=400, bid=5)
    live.status = "OPEN"
    for i in range(3):
        calibration.capture(db, [(_lot(db, a, f"api{i}", low=300, bid=100),
                                  AUCTIONEER)])
    db.commit()
    open_id = open_a.id
    db.close()

    auction = next(x for x in client.get("/auctions").json() if x["id"] == open_id)
    assert auction["estimate_ratio"] == 3.0
    assert auction["estimate_ratio_n"] == 3

    lot = client.get("/lots", params={"auction_id": open_id}).json()[0]
    assert lot["house_ratio"] == 3.0
    assert lot["house_ratio_n"] == 3
