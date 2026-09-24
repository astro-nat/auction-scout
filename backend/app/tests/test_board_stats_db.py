"""The board (`pytest -m db`): /stats/board and the floor setting.

Every counting rule is proven differentially: read the endpoint, insert one
row, read again, assert the EXACT delta. The shared dev database can hold
anything — deltas make each rule's test independent of it.
"""

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app import models
from app.main import app

pytestmark = pytest.mark.db

client = TestClient(app)

TEST_HIBID_OPEN = 999999962
TEST_HIBID_CLOSED = 999999963
PREFIX = "pytest-board-"


def read():
    r = client.get("/stats/board")
    assert r.status_code == 200
    return r.json()


@pytest.fixture()
def auctions():
    """One open and one closed auction to hang lots on; wipes every PREFIX
    lot on the way out so a failed test can't poison the next run."""
    db = SessionLocal()
    open_a = models.Auction(hibid_id=TEST_HIBID_OPEN, name="PYTEST board open",
                            closing_date=datetime.now() + timedelta(days=2),
                            imported_at=datetime.now())
    closed_a = models.Auction(hibid_id=TEST_HIBID_CLOSED, name="PYTEST board closed",
                              closing_date=datetime.now() - timedelta(days=1),
                              imported_at=datetime.now())
    db.add_all([open_a, closed_a])
    db.commit()
    yield open_a.id, closed_a.id
    db2 = SessionLocal()
    ids = [r[0] for r in db2.query(models.Lot.id)
                            .filter(models.Lot.lot_id.like(f"{PREFIX}%")).all()]
    db2.query(models.Enrichment).filter(
        models.Enrichment.lot_id.in_(ids)).delete(synchronize_session=False)
    db2.query(models.Lot).filter(models.Lot.id.in_(ids)).delete(synchronize_session=False)
    db2.query(models.Auction).filter(models.Auction.hibid_id.in_(
        [TEST_HIBID_OPEN, TEST_HIBID_CLOSED])).delete(synchronize_session=False)
    db2.commit()
    db2.close()


def _insert(auction_id, suffix, *, profit, roi_status="PASS", status="OPEN"):
    db = SessionLocal()
    lot = models.Lot(lot_id=f"{PREFIX}{suffix}", auction_id=auction_id,
                     title=f"pytest {suffix}", status=status)
    db.add(lot)
    db.flush()
    db.add(models.Enrichment(lot_id=lot.id, status="success", est_resale=100,
                             profit=profit, roi_status=roi_status))
    db.commit()
    db.close()


def test_open_gold_mine_adds_to_available(auctions):
    open_a, _ = auctions
    before = read()
    _insert(open_a, "gold-open", profit=61.29, roi_status="GOLD MINE")
    after = read()
    assert (after["available_gold_profit"] - before["available_gold_profit"]
            == pytest.approx(61.29))


def test_pass_lot_and_closed_auction_gold_add_nothing(auctions):
    open_a, closed_a = auctions
    before = read()
    _insert(open_a, "pass-open", profit=61.29, roi_status="PASS")
    _insert(closed_a, "gold-closed", profit=61.29, roi_status="GOLD MINE")
    after = read()
    assert after["available_gold_profit"] == pytest.approx(before["available_gold_profit"])


def test_floor_setting_roundtrips_without_regrade():
    before = client.get("/settings").json()
    try:
        r = client.patch("/settings", json={"auction_floor_usd": 150}).json()
        assert r["regrading"] == 0        # display-only: no regrade
        got = client.get("/settings").json()
        assert got["auction_floor_usd"] == 150
        assert got["target_roi_pct"] == before["target_roi_pct"]
        assert read()["auction_floor_usd"] == 150
    finally:
        client.patch("/settings", json={"auction_floor_usd": before["auction_floor_usd"]})


def test_settings_validation_rejects_garbage_without_side_effects():
    before = client.get("/settings").json()
    assert client.patch("/settings", json={}).status_code == 422
    assert client.patch("/settings", json={"auction_floor_usd": -5}).status_code == 422
    assert client.patch("/settings", json={"auction_floor_usd": "lots"}).status_code == 422
    assert client.patch("/settings", json={"target_roi_pct": 0}).status_code == 422
    assert client.get("/settings").json() == before   # nothing half-saved
