"""ships_to_us end to end (`pytest -m db`): the shipping analysis writes it,
/lots serves it per lot, and the analysis endpoint asks again about a
Canadian house whose terms never settled the question."""

import json
import types
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import models
from app.database import SessionLocal
from app.main import app
from app.workers import enrich

pytestmark = pytest.mark.db

client = TestClient(app)
CA_HIBID, US_HIBID = 999999982, 999999981


@pytest.fixture
def houses(monkeypatch):
    db = SessionLocal()
    soon = datetime.now() + timedelta(days=3)
    ca = models.Auction(hibid_id=CA_HIBID, name="ships-to-us-test CA", state="On",
                        source="Ship", closing_date=soon,
                        imported_at=datetime.now(timezone.utc))
    us = models.Auction(hibid_id=US_HIBID, name="ships-to-us-test US", state="TX",
                        source="Ship", closing_date=soon,
                        imported_at=datetime.now(timezone.utc))
    db.add_all([ca, us])
    db.flush()
    lot = models.Lot(lot_id="stu-1", title="Maple syrup tin", auction_id=ca.id,
                     current_bid=1)
    db.add(lot)
    db.commit()
    for name in ("start", "get", "is_cancelled", "update", "finish"):
        monkeypatch.setattr(enrich.jobs, name,
                            {"start": lambda *a, **k: "stu-job", "get": lambda *a, **k: {},
                             "is_cancelled": lambda *a, **k: False}.get(name, lambda *a, **k: None))
    yield db, ca.id, us.id
    db.rollback()
    db.query(models.Lot).filter(models.Lot.id == lot.id).delete(synchronize_session=False)
    db.query(models.Auction).filter(models.Auction.id.in_([ca.id, us.id])).delete(
        synchronize_session=False)
    db.commit()
    db.close()


def _fake_ai(monkeypatch, reply: dict):
    msg = types.SimpleNamespace(content=[types.SimpleNamespace(text=json.dumps(reply))])
    monkeypatch.setattr(enrich, "client", types.SimpleNamespace(
        messages=types.SimpleNamespace(create=lambda **kw: msg)))


def _fake_terms(monkeypatch, by_hibid: dict):
    async def fetch(http, ids):
        return {i: {"ship_text": t, "terms_text": ""} for i, t in by_hibid.items() if i in ids}
    monkeypatch.setattr(enrich.hibid, "fetch_auction_meta", fetch)


def _reload(db, *ids):
    db.expire_all()
    return [db.query(models.Auction).get(i) for i in ids]


def test_the_analysis_reads_the_border_policy(houses, monkeypatch):
    """Plain text wins over the AI; a US house is never flagged."""
    db, ca_id, us_id = houses
    _fake_terms(monkeypatch, {CA_HIBID: "We ship within Canada only.",
                              US_HIBID: "We ship anywhere in the continental US."})
    _fake_ai(monkeypatch, {"ships": True, "cost_estimate": 15, "summary": "Ships",
                           "ships_to_us": True})
    enrich.run_ship_analysis([ca_id, us_id])
    ca, us = _reload(db, ca_id, us_id)
    assert ca.ships_to_us is False
    assert us.ships_to_us is None
    assert ca.ship_analyzed_at is not None


def test_the_ai_answers_when_the_text_does_not(houses, monkeypatch):
    db, ca_id, _ = houses
    _fake_terms(monkeypatch, {CA_HIBID: "Shipping is available; fees quoted after the sale."})
    _fake_ai(monkeypatch, {"ships": True, "cost_estimate": 20, "summary": "Ships",
                           "ships_to_us": False})
    enrich.run_ship_analysis([ca_id])
    (ca,) = _reload(db, ca_id)
    assert ca.ships_to_us is False


def test_lots_carry_the_flag(houses):
    db, ca_id, _ = houses
    ca = db.query(models.Auction).get(ca_id)
    ca.ships_to_us = False
    db.commit()
    r = client.get("/lots", params={"auction_id": ca_id})
    assert r.status_code == 200, r.text
    assert [l["auction_no_us_ship"] for l in r.json()] == [True]
    ca.ships_to_us = None
    db.commit()
    assert client.get("/lots", params={"auction_id": ca_id}).json()[0]["auction_no_us_ship"] is False


def test_an_unsettled_canadian_house_is_asked_again(houses):
    """Already analyzed, border still unknown: the dry run counts it. Once
    the answer is in, it drops out."""
    db, ca_id, us_id = houses
    ca, us = _reload(db, ca_id, us_id)
    ca.ship_analyzed_at = us.ship_analyzed_at = datetime.now()
    ca.ships_to_us = None
    db.commit()
    with_unknown = client.post("/auctions/analyze-shipping", params={"dry_run": "true"}).json()["auctions"]
    ca.ships_to_us = True
    db.commit()
    settled = client.post("/auctions/analyze-shipping", params={"dry_run": "true"}).json()["auctions"]
    assert with_unknown - settled == 1
