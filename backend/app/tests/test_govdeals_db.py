"""GovDeals scan/import endpoints (`pytest -m db`) — network stubbed.

The scan upserts one synthetic auction per seller without touching lots;
the import turns one seller's assets into lots with pending enrichment,
idempotently: a re-import refreshes bids and close times but never
duplicates rows or disturbs analysis fields.
"""

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app import models
from app.main import app
from app.routers import govdeals as gd_router

pytestmark = pytest.mark.db

client = TestClient(app)

PREFIX = "gd-999901"


def _asset(asset_id, *, bid=10.0, title="Vintage Tool Chest Find"):
    return {
        "account_id": 999901, "asset_id": asset_id,
        "lot_id": f"gd-999901-{asset_id}",
        "title": title, "category": "General Merchandise",
        "seller": "PYTEST Agency, TX",
        "city": "Houston", "state": "TX", "zip": "77058",
        "current_bid": bid, "next_bid": bid + 5, "bid_count": 1,
        "closes_at": datetime(2030, 1, 1, 12, 0, 0),
        "thumbnail_url": "https://example.test/p.jpg&w=350",
        "fullsize_url": "https://example.test/p.jpg",
        "lot_link": "https://www.govdeals.com/en/asset/1/999901",
    }


@pytest.fixture()
def cleanup():
    yield
    db = SessionLocal()
    ids = [r[0] for r in db.query(models.Lot.id)
                           .filter(models.Lot.lot_id.like(f"{PREFIX}-%")).all()]
    db.query(models.Enrichment).filter(
        models.Enrichment.lot_id.in_(ids)).delete(synchronize_session=False)
    db.query(models.Lot).filter(models.Lot.id.in_(ids)).delete(synchronize_session=False)
    db.query(models.Auction).filter(
        models.Auction.external_id == PREFIX).delete(synchronize_session=False)
    db.commit()
    db.close()


def _scan_with(monkeypatch, assets):
    monkeypatch.setattr(gd_router.govdeals, "search_assets",
                        lambda *a, **k: assets)
    r = client.post("/govdeals/scan", json={"zip": "77058", "radius_miles": 25})
    assert r.status_code == 200
    return [a for a in r.json() if a.get("external_id") == PREFIX]


def test_scan_makes_one_auction_per_seller_and_no_lots(monkeypatch, cleanup):
    cards = _scan_with(monkeypatch, [_asset(1), _asset(2)])
    assert len(cards) == 1
    card = cards[0]
    assert card["name"] == "GovDeals: PYTEST Agency, TX"
    assert card["source"] == "Local Pickup"
    assert card["lot_count"] == 2
    assert card["lots_imported"] == 0        # scan surfaces, import commits
    # Re-scan updates in place instead of stacking a second row.
    assert len(_scan_with(monkeypatch, [_asset(1)])) == 1


def test_import_is_an_idempotent_bid_refresh(monkeypatch, cleanup):
    card = _scan_with(monkeypatch, [_asset(1, bid=10)])[0]
    monkeypatch.setattr(gd_router.govdeals, "search_assets",
                        lambda *a, **k: [_asset(1, bid=10)])
    r = client.post(f"/govdeals/{card['id']}/import").json()
    assert (r["created"], r["updated"]) == (1, 0)

    db = SessionLocal()
    lot = db.query(models.Lot).filter(models.Lot.lot_id == f"{PREFIX}-1").first()
    assert lot.source == "Local Pickup"
    assert float(lot.current_bid) == 10
    assert lot.enrichment.status == "pending"
    assert lot.logistics_ease in ("EASY", "NEUTRAL", "HARD")
    db.close()

    # Second import: same lot, fresh bid, analysis untouched.
    monkeypatch.setattr(gd_router.govdeals, "search_assets",
                        lambda *a, **k: [_asset(1, bid=45)])
    r = client.post(f"/govdeals/{card['id']}/import").json()
    assert (r["created"], r["updated"]) == (0, 1)
    db = SessionLocal()
    lot = db.query(models.Lot).filter(models.Lot.lot_id == f"{PREFIX}-1").first()
    assert float(lot.current_bid) == 45
    db.close()


def test_import_leaves_other_states_behind(monkeypatch, cleanup):
    card = _scan_with(monkeypatch, [_asset(1)])[0]
    far = dict(_asset(2), state="AZ", city="Phoenix")
    monkeypatch.setattr(gd_router.govdeals, "search_assets",
                        lambda *a, **k: [_asset(1), far])
    r = client.post(f"/govdeals/{card['id']}/import").json()
    assert r["created"] == 1                 # the AZ yard never imports
    db = SessionLocal()
    assert (db.query(models.Lot)
              .filter(models.Lot.lot_id == f"{PREFIX}-2").first()) is None
    db.close()


def test_import_refuses_hibid_auctions(cleanup):
    db = SessionLocal()
    hibid_a = (db.query(models.Auction)
                 .filter(models.Auction.hibid_id.isnot(None)).first())
    db.close()
    if hibid_a is None:
        pytest.skip("no HiBid auction in the dev DB to poke")
    assert client.post(f"/govdeals/{hibid_a.id}/import").status_code == 404
