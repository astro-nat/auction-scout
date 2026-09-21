"""PublicSurplus scan/import endpoints (`pytest -m db`) — network stubbed.

One synthetic auction per search area; import fills it with lots and
doubles as the bid refresh, idempotently.
"""

from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app import models
from app.main import app
from app.routers import publicsurplus as ps_router

pytestmark = pytest.mark.db

client = TestClient(app)

CARD_KEY = "ps-99997"
LOT_PREFIX = "ps-99997000"


def _item(n, *, bid=5.0, title="School Surplus Projector"):
    return {
        "auction_id": int(f"99997000{n}"),
        "lot_id": f"{LOT_PREFIX}{n}",
        "title": title,
        "current_bid": bid,
        "closes_at": datetime(2030, 1, 2, 12, 0, 0),
        "thumbnail_url": "https://example.test/t.jpg",
        "lot_link": "https://www.publicsurplus.com/x",
    }


@pytest.fixture()
def cleanup():
    yield
    db = SessionLocal()
    ids = [r[0] for r in db.query(models.Lot.id)
                           .filter(models.Lot.lot_id.like(f"{LOT_PREFIX}%")).all()]
    db.query(models.Enrichment).filter(
        models.Enrichment.lot_id.in_(ids)).delete(synchronize_session=False)
    db.query(models.Lot).filter(models.Lot.id.in_(ids)).delete(synchronize_session=False)
    db.query(models.Auction).filter(
        models.Auction.external_id == CARD_KEY).delete(synchronize_session=False)
    db.commit()
    db.close()


def _scan_with(monkeypatch, items):
    monkeypatch.setattr(ps_router.publicsurplus, "search_items",
                        lambda *a, **k: items)
    r = client.post("/publicsurplus/scan",
                    json={"zip": "99997", "radius_miles": 25})
    assert r.status_code == 200
    return [a for a in r.json() if a.get("external_id") == CARD_KEY]


def test_scan_makes_one_area_card_without_lots(monkeypatch, cleanup):
    cards = _scan_with(monkeypatch, [_item(1), _item(2)])
    assert len(cards) == 1
    card = cards[0]
    assert card["name"] == "PublicSurplus near 99997 (25 mi)"
    assert card["source"] == "Local Pickup"
    assert card["lot_count"] == 2
    assert card["lots_imported"] == 0
    # Re-scan updates the same card.
    assert len(_scan_with(monkeypatch, [_item(1)])) == 1


def test_import_is_an_idempotent_bid_refresh(monkeypatch, cleanup):
    card = _scan_with(monkeypatch, [_item(1, bid=5)])[0]
    monkeypatch.setattr(ps_router.publicsurplus, "search_items",
                        lambda *a, **k: [_item(1, bid=5)])
    r = client.post(f"/publicsurplus/{card['id']}/import").json()
    assert (r["created"], r["updated"]) == (1, 0)

    db = SessionLocal()
    lot = (db.query(models.Lot)
             .filter(models.Lot.lot_id == f"{LOT_PREFIX}1").first())
    assert lot.source == "Local Pickup"
    assert float(lot.current_bid) == 5
    assert lot.enrichment.status == "pending"
    db.close()

    monkeypatch.setattr(ps_router.publicsurplus, "search_items",
                        lambda *a, **k: [_item(1, bid=17)])
    r = client.post(f"/publicsurplus/{card['id']}/import").json()
    assert (r["created"], r["updated"]) == (0, 1)
    db = SessionLocal()
    lot = (db.query(models.Lot)
             .filter(models.Lot.lot_id == f"{LOT_PREFIX}1").first())
    assert float(lot.current_bid) == 17
    db.close()


def test_import_refuses_non_publicsurplus_auctions(cleanup):
    db = SessionLocal()
    other = (db.query(models.Auction)
               .filter((models.Auction.external_id.is_(None))
                       | ~models.Auction.external_id.like("ps-%")).first())
    db.close()
    if other is None:
        pytest.skip("no non-PublicSurplus auction in the dev DB")
    assert client.post(f"/publicsurplus/{other.id}/import").status_code == 404
