"""A lot whose own closing time has passed is flushed (`pytest -m db`).

Timed sales close lot by lot, long before the auction's final close, and
HiBid's per-lot status only arrives through a bid refresh, which no longer
runs on its own. 727 lots showed "closed" and were never flushed. Runs the
real flush, like test_flush_price_observations_db.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app import models
from app.database import SessionLocal
from app.routers.lots import flush_closed_now

pytestmark = pytest.mark.db

TEST_HIBID = 999999972


@pytest.fixture
def timed_sale():
    db = SessionLocal()
    now = datetime.now()
    auction = models.Auction(hibid_id=TEST_HIBID, name="flush-by-time-test",
                             auctioneer_id=999972,
                             closing_date=now + timedelta(days=2),     # sale still running
                             imported_at=datetime.now(timezone.utc))
    db.add(auction)
    db.flush()
    lots = {
        "long-closed": now - timedelta(hours=2),
        "just-closed": now - timedelta(minutes=10),   # inside the grace: HiBid may extend it
        "still-open": now + timedelta(hours=2),
    }
    ids = {}
    for key, closes in lots.items():
        lot = models.Lot(lot_id=f"fbt-{key}", title=key, auction_id=auction.id,
                         status="OPEN", closes_at=closes, current_bid=20,
                         estimate_low=10, estimate_high=15)
        db.add(lot)
        db.flush()
        db.add(models.Enrichment(lot_id=lot.id, status="pending", user_overrides=[]))
        ids[key] = lot.id
    db.commit()
    yield db, ids
    db.rollback()
    left = list(ids.values())
    db.query(models.EstimateObservation).filter(
        models.EstimateObservation.lot_id.like("fbt-%")).delete(synchronize_session=False)
    db.query(models.Enrichment).filter(
        models.Enrichment.lot_id.in_(left)).delete(synchronize_session=False)
    db.query(models.Lot).filter(models.Lot.id.in_(left)).delete(synchronize_session=False)
    db.query(models.Auction).filter(models.Auction.hibid_id == TEST_HIBID).delete(
        synchronize_session=False)
    db.commit()
    db.close()


def _exists(db, lot_id):
    db.expire_all()
    return db.query(models.Lot.id).filter(models.Lot.id == lot_id).first() is not None


def test_a_lot_past_its_own_close_is_flushed_after_the_grace(timed_sale):
    db, ids = timed_sale
    flush_closed_now(db)
    assert not _exists(db, ids["long-closed"])
    assert _exists(db, ids["just-closed"])
    assert _exists(db, ids["still-open"])


def test_its_stale_bid_is_not_recorded_as_a_hammer_price(timed_sale):
    """Known closed only by the clock: its bid is whatever the last refresh
    saw, not what it sold for, and must not bend the house's estimate ratio."""
    db, ids = timed_sale
    flush_closed_now(db)
    got = (db.query(models.EstimateObservation)
             .filter(models.EstimateObservation.lot_id == "fbt-long-closed").count())
    assert got == 0
