"""Filling in the photo list without importing anything (`pytest -m db`).

image_count came with the first version of the HiBid parser; image_urls
only started being kept on 2026-09-25. So thousands of lots have a photo
count and no list, and the AI is shown one thumbnail of a lot that has
five photos.

A re-import would fix them and also create every lot the original import
filtered out - on this inventory, about 4,300 rows nobody asked for. This
pass writes photos onto lots already on file and touches nothing else.
"""

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app import models
from app.database import SessionLocal
from app.main import app
from app.workers.import_all import backfill_photos

pytestmark = pytest.mark.db

client = TestClient(app)
TEST_HIBID = 999999954
SHOTS = ["https://cdn.example.com/1.jpg", "https://cdn.example.com/2.jpg",
         "https://cdn.example.com/3.jpg"]


def _incoming(lot_id, urls=SHOTS, count=None):
    return {"lot_id": lot_id, "image_urls": urls,
            "image_count": count if count is not None else len(urls),
            "current_bid": 999, "status": "CLOSED", "title": "changed"}


@pytest.fixture
def seeded():
    """Two lots on file, neither with a photo list."""
    db = SessionLocal()
    a = models.Auction(hibid_id=TEST_HIBID, name="photo-fill-test", source="Ship",
                       buyer_premium_mult=1.15,
                       imported_at=datetime.now(timezone.utc))
    db.add(a)
    db.flush()
    for n in (1, 2):
        db.add(models.Lot(lot_id=f"pf-{n}", title=f"Lot {n}", auction_id=a.id,
                          current_bid=3, next_bid=4, status="OPEN",
                          logistics_ease="EASY", source="Ship", image_count=5,
                          thumbnail_url="https://cdn.example.com/t.jpg"))
    db.commit()
    yield db, a
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


def _rows(db, a):
    db.expire_all()
    return {r.lot_id: r for r in db.query(models.Lot)
                                   .filter(models.Lot.auction_id == a.id).all()}


def test_it_fills_the_list_on_the_lots_on_file(seeded):
    db, a = seeded
    filled, matched = backfill_photos(db, a, [_incoming("pf-1"), _incoming("pf-2")])
    assert (filled, matched) == (2, 2)
    for row in _rows(db, a).values():
        assert row.image_urls == SHOTS
        assert row.image_count == 3


def test_a_catalogue_lot_not_on_file_is_never_created(seeded):
    """The whole point: no new rows. A filtered import left those out on
    purpose, and re-importing would bring thousands of them back."""
    db, a = seeded
    filled, matched = backfill_photos(
        db, a, [_incoming("pf-1"), _incoming("pf-NEW-1"), _incoming("pf-NEW-2")])
    assert (filled, matched) == (1, 1)
    assert set(_rows(db, a)) == {"pf-1", "pf-2"}


def test_nothing_but_the_photos_is_written(seeded):
    """The incoming data carries a closed status, a different bid and a
    different title. A backfill is not a refresh."""
    db, a = seeded
    backfill_photos(db, a, [_incoming("pf-1")])
    row = _rows(db, a)["pf-1"]
    assert row.status == "OPEN"
    assert float(row.current_bid) == 3
    assert row.title == "Lot 1"


def test_a_lot_the_source_has_no_photos_for_is_left_alone(seeded):
    db, a = seeded
    filled, matched = backfill_photos(db, a, [_incoming("pf-1", urls=[])])
    assert (filled, matched) == (0, 1)
    assert _rows(db, a)["pf-1"].image_urls is None
    assert _rows(db, a)["pf-1"].image_count == 5      # the old count survives


def test_running_it_twice_fills_nothing_the_second_time(seeded):
    db, a = seeded
    assert backfill_photos(db, a, [_incoming("pf-1")])[0] == 1
    assert backfill_photos(db, a, [_incoming("pf-1")])[0] == 0


def test_the_route_queues_only_auctions_with_lots_on_file(seeded):
    db, a = seeded
    empty = models.Auction(hibid_id=TEST_HIBID + 1, name="no-lots", source="Ship",
                           buyer_premium_mult=1.15,
                           imported_at=datetime.now(timezone.utc))
    db.add(empty)
    db.commit()
    try:
        r = client.post("/auctions/backfill-photos")
        assert r.status_code == 202
        body = r.json()
        if body.get("already_running"):
            pytest.skip("a backfill is already queued in this database")
        assert body["queued"] is True
        db.expire_all()
        job = (db.query(models.Job)
                 .filter(models.Job.kind == "backfill-photos").one())
        ids = (job.payload or {})["auction_ids"]
        assert a.id in ids
        assert empty.id not in ids
        db.query(models.Job).filter(models.Job.id == job.id).delete(
            synchronize_session=False)
        db.commit()
    finally:
        db.query(models.Auction).filter(models.Auction.id == empty.id).delete(
            synchronize_session=False)
        db.commit()
