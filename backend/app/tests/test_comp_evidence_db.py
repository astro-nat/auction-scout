"""The comps round trip (`pytest -m db`): a stored JSONB evidence list comes
back intact through GET /lots, and a legacy row without one serves null —
the contract the evidence panel depends on."""

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app import models
from app.main import app

pytestmark = pytest.mark.db

client = TestClient(app)

TEST_HIBID = 999999981
PREFIX = "pytest-compapi-"


@pytest.fixture()
def seeded():
    db = SessionLocal()
    a = models.Auction(hibid_id=TEST_HIBID, name="PYTEST comps api sale",
                       closing_date=datetime.now() + timedelta(days=2),
                       imported_at=datetime.now())
    db.add(a)
    db.flush()
    rich = models.Lot(lot_id=f"{PREFIX}rich", auction_id=a.id,
                      title="pytest rich", status="OPEN")
    legacy = models.Lot(lot_id=f"{PREFIX}legacy", auction_id=a.id,
                        title="pytest legacy", status="OPEN")
    db.add_all([rich, legacy])
    db.flush()
    db.add(models.Enrichment(
        lot_id=rich.id, status="success", est_resale=42, comp_count=2,
        comps=[{"price": 45.0, "title": "w mint", "url": "https://x/1",
                "date": "2026-08-20", "kind": "sold"},
               {"price": 39.0, "title": "w used", "url": None,
                "date": None, "kind": "sold"}]))
    db.add(models.Enrichment(lot_id=legacy.id, status="success",
                             est_resale=30, comp_count=3, comps=None))
    db.commit()
    yield
    db2 = SessionLocal()
    ids = [r[0] for r in db2.query(models.Lot.id)
                            .filter(models.Lot.lot_id.like(f"{PREFIX}%")).all()]
    db2.query(models.Enrichment).filter(
        models.Enrichment.lot_id.in_(ids)).delete(synchronize_session=False)
    db2.query(models.Lot).filter(models.Lot.id.in_(ids)).delete(synchronize_session=False)
    db2.query(models.Auction).filter(
        models.Auction.hibid_id == TEST_HIBID).delete(synchronize_session=False)
    db2.commit()
    db2.close()
    db.close()


def test_comps_survive_the_api_round_trip(seeded):
    e = client.get(f"/lots/{PREFIX}rich").json()["enrichment"]
    assert len(e["comps"]) == 2
    assert e["comps"][0] == {"price": 45.0, "title": "w mint",
                             "url": "https://x/1", "date": "2026-08-20",
                             "kind": "sold"}
    assert e["comps"][1]["url"] is None


def test_legacy_rows_serve_null_not_an_error(seeded):
    e = client.get(f"/lots/{PREFIX}legacy").json()["enrichment"]
    assert e["comps"] is None
    assert e["comp_count"] == 3        # the count survives from before
