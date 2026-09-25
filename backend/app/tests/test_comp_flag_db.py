"""Flagging a lot's comps as wrong (`pytest -m db`) — the user's own
signal, independent of a hand-corrected value, and the worklist filter
that surfaces every lot flagged this way.
"""

import pytest
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app import models
from app.main import app

pytestmark = pytest.mark.db

client = TestClient(app)

TEST_HIBID = 999999990
PREFIX = "pytest-compflag-"


@pytest.fixture()
def lot():
    db = SessionLocal()
    a = models.Auction(hibid_id=TEST_HIBID, name="PYTEST comp-flag sale")
    db.add(a)
    db.commit()
    row = models.Lot(lot_id=f"{PREFIX}1", auction_id=a.id, title="Widget",
                     status="OPEN")
    db.add(row)
    db.flush()
    db.add(models.Enrichment(lot_id=row.id, status="success", est_resale=50,
                             comp_count=3, roi_status="GOLD MINE"))
    db.commit()
    lot_id = row.lot_id
    db.close()
    yield lot_id
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


def test_flagging_sets_the_flag_and_note(lot):
    r = client.post(f"/lots/{lot}/flag-comp",
                    json={"note": "comp is a different model entirely"})
    assert r.status_code == 200
    e = r.json()["enrichment"]
    assert e["comp_flagged"] is True
    assert e["comp_flag_note"] == "comp is a different model entirely"


def test_flagging_needs_no_note(lot):
    r = client.post(f"/lots/{lot}/flag-comp", json={})
    assert r.status_code == 200
    e = r.json()["enrichment"]
    assert e["comp_flagged"] is True
    assert e["comp_flag_note"] is None


def test_unflagging_clears_the_note_too(lot):
    client.post(f"/lots/{lot}/flag-comp", json={"note": "wrong item entirely"})
    r = client.post(f"/lots/{lot}/flag-comp", json={"flagged": False})
    e = r.json()["enrichment"]
    assert e["comp_flagged"] is False
    assert e["comp_flag_note"] is None


def test_flagging_does_not_touch_the_value_or_overrides(lot):
    r = client.post(f"/lots/{lot}/flag-comp", json={"note": "x"})
    e = r.json()["enrichment"]
    assert float(e["est_resale"]) == 50
    assert e["roi_status"] == "GOLD MINE"
    assert e["user_overrides"] == []


def test_unknown_lot_is_a_404():
    assert client.post("/lots/no-such-lot/flag-comp",
                       json={}).status_code == 404


def test_flagged_only_filters_the_list_and_count(lot):
    before = client.get("/lots/count", params={"flagged_only": True}).json()
    client.post(f"/lots/{lot}/flag-comp", json={"note": "x"})
    after = client.get("/lots/count", params={"flagged_only": True}).json()
    assert after["total"] - before["total"] == 1

    rows = client.get("/lots", params={"flagged_only": True,
                                       "limit": 2000}).json()
    assert any(r["lot_id"] == lot for r in rows)
    assert all(r["enrichment"]["comp_flagged"] for r in rows)
