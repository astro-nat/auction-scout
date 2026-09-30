"""Auctions with no coordinates get them from HiBid (`pytest -m db`).

Reported: the drive-from address was saved and no auction showed a drive
time - every auction in the inventory predated stored locations, and only
a scan brought them. The backfill asks HiBid by event id instead.
"""

from datetime import datetime, timedelta

import pytest

from app import models
from app.database import SessionLocal
from app.services import drive, hibid

pytestmark = pytest.mark.db

PLACED, UNPLACEABLE, CLOSED, VINTED_NAME = 999999861, 999999862, 999999863, "Vinted: backfill-test"
IDS = (PLACED, UNPLACEABLE, CLOSED)


def _clean(db):
    db.query(models.Auction).filter(
        (models.Auction.hibid_id.in_(IDS)) | (models.Auction.name == VINTED_NAME)
    ).delete(synchronize_session=False)
    db.commit()


@pytest.fixture
def db():
    s = SessionLocal()
    _clean(s)
    yield s
    _clean(s)
    s.close()


def test_backfill_places_open_hibid_auctions_and_marks_the_rest(db, monkeypatch):
    soon = datetime.utcnow() + timedelta(days=3)
    long_ago = datetime.utcnow() - timedelta(days=30)
    db.add_all([
        models.Auction(hibid_id=PLACED, name="Placed", closing_date=soon),
        models.Auction(hibid_id=UNPLACEABLE, name="Nowhere", closing_date=soon),
        models.Auction(hibid_id=CLOSED, name="Long closed", closing_date=long_ago),
        models.Auction(hibid_id=None, name=VINTED_NAME, external_id="vt-backfill-test"),
    ])
    db.commit()
    asked = []

    async def fake_locations(ids):
        asked.extend(ids)
        return {PLACED: {"geo_lat": 29.5, "geo_lng": -95.1, "address": "706 Curtis Ave"},
                UNPLACEABLE: {"geo_lat": 0, "geo_lng": 0, "address": None}}

    monkeypatch.setattr(hibid, "fetch_locations", fake_locations)
    drive.backfill_locations(db)

    assert set(asked) & set(IDS) == {PLACED, UNPLACEABLE}, "asked about a closed or non-HiBid sale"
    rows = {a.hibid_id: a for a in db.query(models.Auction).filter(models.Auction.hibid_id.in_(IDS))}
    assert (rows[PLACED].geo_lat, rows[PLACED].geo_lng) == (29.5, -95.1)
    assert rows[PLACED].address == "706 Curtis Ave"
    # Marked, so the next load doesn't ask again - and never measured.
    assert (rows[UNPLACEABLE].geo_lat, rows[UNPLACEABLE].geo_lng) == (0.0, 0.0)
    assert not drive.usable_coords(rows[UNPLACEABLE].geo_lat, rows[UNPLACEABLE].geo_lng)
    assert rows[CLOSED].geo_lat is None

    asked.clear()
    drive.backfill_locations(db)
    assert not (set(asked) & set(IDS)), "asked again about auctions already settled"
