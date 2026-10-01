"""Pickup instructions, only for auctions close enough to pick up from
(`pytest -m db`)."""

from datetime import datetime, timedelta

import pytest

from app import models
from app.database import SessionLocal
from app.services import pickup

pytestmark = pytest.mark.db
NEAR, FAR, UNKNOWN = 999999881, 999999882, 999999883


@pytest.fixture
def db():
    s = SessionLocal()

    def clean():
        s.query(models.Auction).filter(
            models.Auction.hibid_id.in_((NEAR, FAR, UNKNOWN))).delete(synchronize_session=False)
        s.commit()
    clean()
    yield s
    clean()
    s.close()


def _sale(db, hibid_id, minutes):
    a = models.Auction(hibid_id=hibid_id, name=f"Pickup {hibid_id}", source="Local Pickup",
                       drive_minutes=minutes,
                       closing_date=datetime.utcnow() + timedelta(days=2))
    db.add(a)
    db.commit()
    return a


def test_only_auctions_within_45_minutes_are_asked(db, monkeypatch):
    near, far, unknown = _sale(db, NEAR, 36), _sale(db, FAR, 52), _sale(db, UNKNOWN, None)
    asked = []

    async def fake_fetch(client, ids):
        asked.extend(ids)
        return {i: {"pickup_info": "Pick up Friday 9-4", "address": "1 Main St",
                    "city": "Alvin", "state": "TX", "zip": "77511"} for i in ids}

    monkeypatch.setattr("app.services.hibid.fetch_pickup", fake_fetch)
    pickup.refresh(db)
    assert NEAR in asked and FAR not in asked and UNKNOWN not in asked
    db.expire_all()
    assert db.get(models.Auction, near.id).pickup_info == "Pick up Friday 9-4"
    assert db.get(models.Auction, far.id).pickup_info is None

    # Asked once, not again on every page load.
    asked.clear()
    pickup.refresh(db)
    assert asked == []
