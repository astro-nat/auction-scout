"""Releasing the AI lock on one lot (`pytest -m db`).

The lock stops a lot being paid for twice, and it is absolute on every
other route. This is the one way past it: a single lot_id, so each
re-check is a decision about that lot. It exists because a first run can
be wrong for a reason since fixed - a CD case priced as an empty
organiser because the router did not see a container full of discs.
"""

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app import models
from app.database import SessionLocal
from app.main import app

pytestmark = pytest.mark.db

client = TestClient(app)
TEST_HIBID = 999999951


@pytest.fixture
def lot():
    db = SessionLocal()
    a = models.Auction(hibid_id=TEST_HIBID, name="recheck-test", source="Ship",
                       buyer_premium_mult=1.15,
                       imported_at=datetime.now(timezone.utc))
    db.add(a)
    db.flush()
    row = models.Lot(lot_id="rc-1", title="Zippered CD Storage Case With Music CDs",
                     auction_id=a.id, current_bid=1, next_bid=2,
                     logistics_ease="EASY", source="Ship",
                     thumbnail_url="https://example.com/t.jpg")
    db.add(row)
    db.flush()
    db.add(models.Enrichment(lot_id=row.id, status="success", ai_source="text",
                             est_resale=8.0, user_overrides=[]))
    db.commit()
    yield db, row
    db.rollback()
    db.query(models.Enrichment).filter(
        models.Enrichment.lot_id == row.id).delete(synchronize_session=False)
    db.query(models.Lot).filter(models.Lot.id == row.id).delete(synchronize_session=False)
    db.query(models.Auction).filter(models.Auction.id == a.id).delete(
        synchronize_session=False)
    db.commit()
    db.close()


def _enrichment(db, row):
    db.expire_all()
    return db.query(models.Enrichment).filter(
        models.Enrichment.lot_id == row.id).one()


def test_the_other_routes_still_refuse_a_locked_lot(lot):
    for path in ("comps", "enrich", "inspect"):
        r = client.post(f"/lots/rc-1/{path}")
        assert r.status_code == 409, path


def test_a_recheck_releases_the_lock_and_requeues(lot):
    db, row = lot
    r = client.post("/lots/rc-1/recheck")
    assert r.status_code == 202
    assert r.json() == {"lot_id": "rc-1", "status": "queued", "unlocked": True}

    e = _enrichment(db, row)
    assert e.ai_source is None        # the lock is off
    assert e.status == "queued"
    assert e.queued_task == "enrich"  # re-routes; a pile lands on inspect
    assert e.queue_rank == 0
    assert e.claimed_at is None


def test_an_unpriced_lot_recheck_is_harmless_and_says_it_was_not_locked(lot):
    db, row = lot
    e = _enrichment(db, row)
    e.ai_source = None
    e.status = "pending"
    db.commit()
    r = client.post("/lots/rc-1/recheck")
    assert r.json()["unlocked"] is False
    assert _enrichment(db, row).status == "queued"


def test_a_lot_already_queued_is_left_alone(lot):
    db, row = lot
    e = _enrichment(db, row)
    e.status = "queued"
    e.queued_task = "comps"
    db.commit()
    r = client.post("/lots/rc-1/recheck")
    assert r.json() == {"lot_id": "rc-1", "status": "queued", "unlocked": False}
    assert _enrichment(db, row).queued_task == "comps"   # not stolen


def test_an_unknown_lot_is_a_404(lot):
    assert client.post("/lots/nope-999/recheck").status_code == 404
