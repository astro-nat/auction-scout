"""The forgotten-auctions list clears itself after a week (`pytest -m db`).

An entry forgotten more than seven days ago is dropped - unless its auction
is known to still be open, because then the next scan would bring the sale
straight back.
"""

from datetime import datetime, timedelta

import pytest

from app import models
from app.database import SessionLocal
from app.services import dismissed

pytestmark = pytest.mark.db

OLD_CLOSED, OLD_OPEN, OLD_UNKNOWN, RECENT = 999999871, 999999872, 999999873, 999999874
IDS = (OLD_CLOSED, OLD_OPEN, OLD_UNKNOWN, RECENT)
NOW = datetime(2026, 10, 1, 12, 0)


def _clean(db):
    db.query(models.DismissedAuction).filter(
        models.DismissedAuction.hibid_id.in_(IDS)).delete(synchronize_session=False)
    db.query(models.Auction).filter(
        models.Auction.hibid_id.in_(IDS)).delete(synchronize_session=False)
    db.commit()


@pytest.fixture
def db():
    s = SessionLocal()
    _clean(s)
    yield s
    _clean(s)
    s.close()


def _forget(db, hibid_id, days_ago, closes_in_days=None):
    if closes_in_days is not None:
        db.add(models.Auction(hibid_id=hibid_id, name=f"Sale {hibid_id}",
                              closing_date=NOW + timedelta(days=closes_in_days)))
    db.add(models.DismissedAuction(hibid_id=hibid_id, name=f"Sale {hibid_id}",
                                   created_at=NOW - timedelta(days=days_ago)))
    db.commit()


def test_a_week_old_entry_goes_unless_its_sale_is_still_running(db):
    _forget(db, OLD_CLOSED, days_ago=8, closes_in_days=-3)
    _forget(db, OLD_OPEN, days_ago=8, closes_in_days=5)
    _forget(db, OLD_UNKNOWN, days_ago=10)            # auction row already purged
    _forget(db, RECENT, days_ago=2, closes_in_days=-1)

    dismissed.prune(db, now=NOW)

    left = {r[0] for r in db.query(models.DismissedAuction.hibid_id)
                           .filter(models.DismissedAuction.hibid_id.in_(IDS))}
    assert left == {OLD_OPEN, RECENT}


def test_reading_the_list_prunes_it(db):
    _forget(db, OLD_UNKNOWN, days_ago=30)
    assert OLD_UNKNOWN not in dismissed.ids(db)
