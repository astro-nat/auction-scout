"""API tests that hit the real dev database (run inside the backend
container: `docker compose exec backend pytest -m db`). Each test seeds
rows under throwaway hibid_ids and cleans up after itself.
"""

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app import models
from app.main import app

pytestmark = pytest.mark.db

client = TestClient(app)

TEST_HIBID = 999999950


@pytest.fixture()
def seeded_auction():
    db = SessionLocal()
    a = models.Auction(hibid_id=TEST_HIBID, name="PYTEST auction",
                       closing_date=datetime.now() + timedelta(days=2),
                       imported_at=datetime.now())
    db.add(a)
    db.flush()
    lots = []
    for i, status in enumerate(["OPEN", "CLOSED"]):
        lot = models.Lot(lot_id=f"pytest-{i}", auction_id=a.id,
                         title=f"pytest lot {i}", status=status)
        db.add(lot)
        db.flush()
        db.add(models.Enrichment(lot_id=lot.id, status="pending"))
        lots.append(lot.id)
    db.commit()
    yield a.id, lots
    db2 = SessionLocal()
    for lot in db2.query(models.Lot).filter(models.Lot.auction_id == a.id).all():
        if lot.enrichment:
            db2.delete(lot.enrichment)
        db2.delete(lot)
    row = db2.query(models.Auction).filter(models.Auction.hibid_id == TEST_HIBID).first()
    if row:
        db2.delete(row)
    db2.commit()
    db2.close()
    db.close()


def test_flush_removes_closed_lot_but_keeps_open_sibling(seeded_auction):
    auction_id, _ = seeded_auction
    r = client.post("/lots/flush-closed?dry_run=true").json()
    assert r["lots"] >= 1  # at least our CLOSED-status lot qualifies
    client.post("/lots/flush-closed")
    db = SessionLocal()
    left = [l.lot_id for l in db.query(models.Lot)
            .filter(models.Lot.auction_id == auction_id).all()]
    db.close()
    assert "pytest-0" in left      # OPEN lot survives
    assert "pytest-1" not in left  # CLOSED lot flushed


def test_watch_and_hide_roundtrip(seeded_auction):
    r = client.post("/lots/pytest-0/watch?watched=true").json()
    assert r["watched"] is True
    r = client.post("/lots/pytest-0/watch?watched=false").json()
    assert r["watched"] is False
    r = client.post("/lots/pytest-0/hide?hidden=true").json()
    assert r["hidden"] is True
    r = client.post("/lots/pytest-0/hide?hidden=false").json()
    assert r["hidden"] is False


def test_settings_validation():
    assert client.patch("/settings", json={"target_roi_pct": "junk"}).status_code == 422
    assert client.patch("/settings", json={"target_roi_pct": -5}).status_code == 422


def test_lots_limit_clamped():
    r = client.get("/lots?limit=6000")
    assert r.status_code == 200
    assert len(r.json()) <= 2000
