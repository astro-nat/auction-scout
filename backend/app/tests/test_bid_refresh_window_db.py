"""Which auctions are due a bid refresh (`pytest -m db`).

Every refresh re-fetches every lot of an auction from HiBid, so pulling a
500-lot sale hourly for a week costs a great deal to learn nothing: bids
barely move until the close. The window limits that.

The subtlety is that HiBid staggers closings — an auction stays open for
days while its lots finish in waves — so the lot's own close time decides,
not the auction's. Except on webcast sales, which carry no per-lot countdown
at all.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app import models
from app.database import SessionLocal
from app.workers.refresh import auctions_due_for_bid_refresh

pytestmark = pytest.mark.db

HIBID_BASE = 999999960


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


@pytest.fixture
def db():
    s = SessionLocal()
    yield s
    ids = [r[0] for r in s.query(models.Auction.id)
                          .filter(models.Auction.hibid_id >= HIBID_BASE).all()]
    if ids:
        s.query(models.Lot).filter(models.Lot.auction_id.in_(ids)).delete(
            synchronize_session=False)
        s.query(models.Auction).filter(models.Auction.id.in_(ids)).delete(
            synchronize_session=False)
        s.commit()
    s.close()


def _auction(db, n, closing_in_hours, lot_closes_in_hours=None, lots=2):
    """An auction with `lots` lots, optionally carrying per-lot close times."""
    a = models.Auction(hibid_id=HIBID_BASE + n, name=f"window-test-{n}",
                       closing_date=(_utcnow() + timedelta(hours=closing_in_hours)
                                     if closing_in_hours is not None else None))
    db.add(a)
    db.flush()
    for i in range(lots):
        db.add(models.Lot(
            lot_id=f"wt{n}-{i}", title="x", auction_id=a.id,
            closes_at=(_utcnow() + timedelta(hours=lot_closes_in_hours)
                       if lot_closes_in_hours is not None else None)))
    db.commit()
    return a.id


def test_a_lot_closing_soon_pulls_its_auction_in(db):
    aid = _auction(db, 1, closing_in_hours=48, lot_closes_in_hours=0.5)
    assert aid in auctions_due_for_bid_refresh(db, window_hours=1)


def test_an_auction_days_out_is_left_alone(db):
    aid = _auction(db, 2, closing_in_hours=48, lot_closes_in_hours=48)
    assert aid not in auctions_due_for_bid_refresh(db, window_hours=1)


def test_the_lot_time_beats_the_auction_time(db):
    """The whole point: the sale runs for days, this lot closes in minutes."""
    aid = _auction(db, 3, closing_in_hours=72, lot_closes_in_hours=0.25)
    assert aid in auctions_due_for_bid_refresh(db, window_hours=1)


def test_a_webcast_with_no_lot_times_falls_back_to_the_auction_date(db):
    """600 of the lots on file have no closes_at. Keying only off lot times
    would mean that auction never refreshes at all."""
    soon = _auction(db, 4, closing_in_hours=0.5, lot_closes_in_hours=None)
    later = _auction(db, 5, closing_in_hours=72, lot_closes_in_hours=None)
    due = auctions_due_for_bid_refresh(db, window_hours=1)
    assert soon in due
    assert later not in due


def test_an_auction_with_no_dates_at_all_is_included(db):
    """Unknown timing can't be ruled out, and silently never refreshing is
    the worse failure."""
    aid = _auction(db, 6, closing_in_hours=None, lot_closes_in_hours=None)
    assert aid in auctions_due_for_bid_refresh(db, window_hours=1)


def test_a_zero_window_means_everything_still_open(db):
    far = _auction(db, 7, closing_in_hours=100, lot_closes_in_hours=100)
    assert far in auctions_due_for_bid_refresh(db, window_hours=0)


def test_closed_auctions_are_never_refreshed(db):
    aid = _auction(db, 8, closing_in_hours=-5, lot_closes_in_hours=-5)
    assert aid not in auctions_due_for_bid_refresh(db, window_hours=0)
    assert aid not in auctions_due_for_bid_refresh(db, window_hours=1)


def test_a_lot_that_already_closed_does_not_qualify_its_auction(db):
    """Past close times are not 'within the next hour'."""
    aid = _auction(db, 9, closing_in_hours=72, lot_closes_in_hours=-2)
    assert aid not in auctions_due_for_bid_refresh(db, window_hours=1)
