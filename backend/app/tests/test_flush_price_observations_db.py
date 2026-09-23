"""Flushing closed lots must take their price observations with them (`pytest -m db`).

price_observations was added after the flush was written. Its foreign key
to lots has no cascade, and the flush deleted enrichments then lots - so
the first closed auction whose lots carried a price trail failed the whole
flush on the constraint and rolled it back. After a day of re-pricing,
that was nearly every closed auction.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app import models
from app.database import SessionLocal
from app.routers.lots import flush_closed_now
from app.services import price_log

pytestmark = pytest.mark.db

TEST_HIBID = 999999980


@pytest.fixture
def closed_lot():
    db = SessionLocal()
    auction = models.Auction(hibid_id=TEST_HIBID, name="flush-obs-test",
                             closing_date=datetime.now() - timedelta(days=1),
                             imported_at=datetime.now(timezone.utc))
    db.add(auction)
    db.flush()
    lot = models.Lot(lot_id="fo-1", title="Flush Test Widget",
                     auction_id=auction.id, current_bid=2, status="CLOSED")
    db.add(lot)
    db.flush()
    db.add(models.Enrichment(lot_id=lot.id, status="success", est_resale=20))
    db.commit()
    price_log.record(lot.id, 20.0, method="comps", price_source="sold (SoldComps)")
    price_log.record(lot.id, None, method="empty", chosen=False, note="kept $20.00")
    lot_id, auction_id = lot.id, auction.id
    yield db, lot_id, auction_id
    db.rollback()
    db.query(models.PriceObservation).filter(
        models.PriceObservation.lot_id == lot_id).delete(synchronize_session=False)
    db.query(models.Enrichment).filter(
        models.Enrichment.lot_id == lot_id).delete(synchronize_session=False)
    db.query(models.Lot).filter(models.Lot.id == lot_id).delete(synchronize_session=False)
    db.query(models.Auction).filter(models.Auction.id == auction_id).delete(
        synchronize_session=False)
    db.commit()
    db.close()


def test_a_closed_lot_with_a_price_trail_can_be_flushed(closed_lot):
    """The failure: IntegrityError on price_observations.lot_id, and a
    rolled-back flush that deleted nothing."""
    db, lot_id, _ = closed_lot
    assert db.query(models.PriceObservation).filter(
        models.PriceObservation.lot_id == lot_id).count() == 2
    result = flush_closed_now(db)          # must not raise
    assert result["lots"] >= 1
    db.expire_all()
    assert db.query(models.Lot).filter(models.Lot.id == lot_id).first() is None
    assert db.query(models.PriceObservation).filter(
        models.PriceObservation.lot_id == lot_id).count() == 0


def test_dry_run_leaves_the_trail_alone(closed_lot):
    db, lot_id, _ = closed_lot
    flush_closed_now(db, dry_run=True)
    db.expire_all()
    assert db.query(models.PriceObservation).filter(
        models.PriceObservation.lot_id == lot_id).count() == 2
