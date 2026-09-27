"""A burner account's listing never earns a gold badge (`pytest -m db`).

The arithmetic loves these: a fresh Vinted account asking $65 for $450 of
SSDs is a gold mine by every number the app computes. That is exactly
backwards - the price is the bait. The lot still shows and still carries
its value; only the badge, which says "buy this", is withheld, and the
reason says why.
"""

from datetime import datetime, timezone

import pytest

from app import models
from app.database import SessionLocal
from app.workers.enrich import _apply_roi

pytestmark = pytest.mark.db

TEST_HIBID = 999999911


def _lot(db, auction, **seller):
    row = models.Lot(lot_id=f"sg-{seller.get('tag', 'x')}", title="Nintendo Switch OLED",
                     auction_id=auction.id, current_bid=40, next_bid=41,
                     status="OPEN", logistics_ease="EASY", source="Ship",
                     seller_id="123", **{k: v for k, v in seller.items() if k != 'tag'})
    db.add(row)
    db.flush()
    e = models.Enrichment(lot_id=row.id, status="success", est_resale=300,
                          comp_count=8, price_source="sold (SoldComps)",
                          user_overrides=[])
    db.add(e)
    db.flush()
    return row, e


def _purge(db):
    """A run that dies in setup leaves its rows behind; start from clean."""
    old = db.query(models.Auction).filter(models.Auction.hibid_id == TEST_HIBID).all()
    for a in old:
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
    a = models.Auction(hibid_id=TEST_HIBID, name="seller-gate-test", source="Ship",
                       buyer_premium_mult=1.05,
                       imported_at=datetime.now(timezone.utc))
    db.add(a)
    db.commit()
    yield db, a
    db.rollback()
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
    db.close()


def test_a_real_seller_keeps_the_badge(auction):
    db, a = auction
    lot, e = _lot(db, a, tag='real', seller_rating=5.0, seller_feedback_count=27,
                  seller_item_count=51, seller_bought_count=26)
    _apply_roi(lot, e)
    assert e.roi_status == "GOLD MINE"
    assert e.roi_reason is None


def test_a_burner_account_does_not(auction):
    db, a = auction
    lot, e = _lot(db, a, tag='burner', seller_rating=None, seller_feedback_count=None,
                  seller_item_count=1, seller_bought_count=0)
    _apply_roi(lot, e)
    assert e.roi_status == "PASS"
    assert e.roi_reason == ("seller has no ratings, 1 listing and has never "
                            "bought anything")


def test_the_value_and_the_ceiling_survive(auction):
    """Only the badge is withheld. The lot is still worth looking at, and
    the numbers are still how you look at it."""
    db, a = auction
    lot, e = _lot(db, a, tag='keep', seller_feedback_count=None,
                  seller_item_count=1, seller_bought_count=0)
    _apply_roi(lot, e)
    assert float(e.est_resale) == 300
    assert e.max_bid is not None and float(e.max_bid) > 0
    assert e.est_roi is not None


def test_a_seller_we_could_not_look_up_keeps_the_benefit_of_the_doubt(auction):
    """All four stats None is "unknown", not "no history" - the lookup can
    fail, and a failed lookup must not condemn a seller."""
    db, a = auction
    lot, e = _lot(db, a, tag='unknown')
    _apply_roi(lot, e)
    assert e.roi_status == "GOLD MINE"


def test_a_non_vinted_lot_is_unaffected(auction):
    """Auction-house lots have no seller columns at all."""
    db, a = auction
    lot, e = _lot(db, a, tag='house')
    lot.seller_id = None
    _apply_roi(lot, e)
    assert e.roi_status == "GOLD MINE"
