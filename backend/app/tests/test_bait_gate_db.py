"""Bait does not earn a gold badge either (`pytest -m db`).

The seller rule catches the account; this catches the listing. They are
complementary: of the 12 bait listings on file, 10 belonged to burner
accounts and 2 did not - including the SSD bundle from a seller with real
ratings, a real closet and a listing whose numbers do not work.

Only where the price is an ASK. An auction lot opens at a dollar and
climbs, so a $1 bid against a $500 value is an ordinary Tuesday.
"""

from datetime import datetime, timezone

import pytest

from app import models
from app.database import SessionLocal
from app.workers.enrich import _apply_roi

pytestmark = pytest.mark.db

TEST_HIBID = 999999891


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
def make():
    scrub = SessionLocal()
    _purge(scrub)
    scrub.close()
    db = SessionLocal()
    created = {}

    def build(auctioneer, ask, value, tag):
        a = created.get(auctioneer)
        if a is None:
            a = models.Auction(hibid_id=TEST_HIBID, name=f"bait-{auctioneer}",
                               auctioneer=auctioneer, source="Ship",
                               buyer_premium_mult=1.05,
                               imported_at=datetime.now(timezone.utc))
            db.add(a)
            db.flush()
            created[auctioneer] = a
        lot = models.Lot(lot_id=f"bg-{tag}", title="Apple Mac Studio 2022",
                         auction_id=a.id, current_bid=ask, next_bid=ask,
                         status="OPEN", logistics_ease="EASY", source="Ship")
        db.add(lot)
        db.flush()
        e = models.Enrichment(lot_id=lot.id, status="success", est_resale=value,
                              comp_count=9, price_source="sold (SoldComps)",
                              user_overrides=[])
        db.add(e)
        db.flush()
        return lot, e

    yield db, build
    db.rollback()
    _purge(db)
    db.close()


def test_a_bait_listing_loses_the_badge(make):
    db, build = make
    lot, e = build("Vinted", 213, 1200, "bait")
    _apply_roi(lot, e)
    assert e.roi_status == "PASS"
    assert "its own comps value at" in e.roi_reason
    assert "$213" in e.roi_reason and "$1,200" in e.roi_reason


def test_an_ordinary_vinted_bargain_is_not_called_bait(make):
    """Half price is the app doing its job. Whether it also clears the ROI
    target is a separate question - what matters here is that this gate
    stays quiet."""
    db, build = make
    lot, e = build("Vinted", 150, 300, "deal")
    _apply_roi(lot, e)
    assert "its own comps value at" not in (e.roi_reason or "")


def test_a_cheap_media_lot_is_not_bait(make):
    """The deepest discounts on file are $1 DVD bundles the comps value at
    $23 - 4% of it. A dollar is a dollar, and this gate must not touch
    them however deep the ratio looks."""
    db, build = make
    lot, e = build("Vinted", 1, 24.99, "dvd")
    _apply_roi(lot, e)
    assert "its own comps value at" not in (e.roi_reason or "")


def test_an_auction_lot_is_never_judged_this_way(make):
    """Same numbers, but the price is a BID on its way up."""
    db, build = make
    lot, e = build("Heartland Auctions", 213, 1200, "auction")
    _apply_roi(lot, e)
    assert e.roi_status == "GOLD MINE"
    assert e.roi_reason is None


def test_the_value_and_the_ceiling_survive(make):
    db, build = make
    lot, e = build("Vinted", 213, 1200, "keep")
    _apply_roi(lot, e)
    assert float(e.est_resale) == 1200
    assert e.max_bid is not None and float(e.max_bid) > 0
