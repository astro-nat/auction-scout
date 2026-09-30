"""Vinted watch endpoint (`pytest -m db`) — network stubbed.

One card per query; scanning imports in the same call; a rescan
refreshes asks and closes out whatever left the results (sold or
delisted, either way not buyable).
"""

import pytest
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app import models
from app.main import app
from app.routers import vinted as vt_router

pytestmark = pytest.mark.db

client = TestClient(app)

QUERY = "pytest vinted watch"
CARD_KEY = "vt-pytest-vinted-watch"
LOT_PREFIX = "vt-99996000"


def _item(n, *, price=20.0, title="Le Creuset Dutch Oven 5.5qt"):
    return {
        "item_id": int(f"99996000{n}"),
        "lot_id": f"{LOT_PREFIX}{n}",
        "title": title, "brand": "Le Creuset", "condition": "Very good",
        "price": price,
        "thumbnail_url": "https://example.test/t.webp",
        "lot_link": "https://www.vinted.com/items/1",
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


def _scan_with(monkeypatch, items, complete=True):
    """search_items reports coverage as well as results: the close-out only
    runs over a complete read, because a lot missing from a window that
    stopped early has not been shown to be sold."""
    monkeypatch.setattr(vt_router.vinted, "search_items",
                        lambda *a, **k: {"items": items, "complete": complete,
                                         "pages_read": 1 if complete else 2,
                                         "total_pages": 1 if complete else 10,
                                         "total_entries": len(items)})
    r = client.post("/vinted/scan", json={"query": QUERY})
    assert r.status_code == 200
    return [a for a in r.json() if a.get("external_id") == CARD_KEY]


def test_scan_imports_in_one_call(monkeypatch, cleanup):
    card = _scan_with(monkeypatch, [_item(1), _item(2)])[0]
    assert card["name"] == f"Vinted: {QUERY}"
    assert card["lots_imported"] == 2
    db = SessionLocal()
    lot = (db.query(models.Lot)
             .filter(models.Lot.lot_id == f"{LOT_PREFIX}1").first())
    assert float(lot.current_bid) == 20.0        # the asking price IS the bid
    assert float(lot.next_bid) == 20.0
    assert lot.enrichment.status == "pending"
    assert "Le Creuset" in (lot.description or "")
    assert "Very good" in (lot.description or "")
    db.close()


def test_rescan_refreshes_asks_and_closes_sold_items(monkeypatch, cleanup):
    _scan_with(monkeypatch, [_item(1, price=20), _item(2)])
    # Next scan: item 1 got repriced, item 2 vanished (sold), item 3 is new.
    _scan_with(monkeypatch, [_item(1, price=35), _item(3)])
    db = SessionLocal()
    by = {l.lot_id: l for l in db.query(models.Lot)
          .filter(models.Lot.lot_id.like(f"{LOT_PREFIX}%")).all()}
    assert float(by[f"{LOT_PREFIX}1"].current_bid) == 35
    assert by[f"{LOT_PREFIX}1"].status == "OPEN"
    assert by[f"{LOT_PREFIX}2"].status == "CLOSED"
    assert by[f"{LOT_PREFIX}3"].status == "OPEN"
    db.close()


def test_blank_query_is_refused(cleanup):
    assert client.post("/vinted/scan", json={"query": "  "}).status_code == 422


# --- absence is only evidence over a complete read -----------------------

def _statuses(db):
    return {l.lot_id: l.status for l in db.query(models.Lot)
            .filter(models.Lot.lot_id.like(f"{LOT_PREFIX}%")).all()}


def test_a_complete_rescan_closes_what_is_gone(monkeypatch, cleanup):
    """Vinted's search never shows a sold item, so over the WHOLE result set
    a listing that stopped appearing has sold or been delisted."""
    _scan_with(monkeypatch, [_item(1), _item(2)])
    _scan_with(monkeypatch, [_item(1)], complete=True)
    db = SessionLocal()
    got = _statuses(db)
    db.close()
    assert got[f"{LOT_PREFIX}1"] == "OPEN"
    assert got[f"{LOT_PREFIX}2"] == "CLOSED"


def test_a_truncated_rescan_closes_nothing(monkeypatch, cleanup):
    """The bug this fixes. Two pages of a ten-page search is 20% of it; a lot
    absent from that window has not been shown to be sold, it has been shown
    to be further down the list. Closing it loses a live listing."""
    _scan_with(monkeypatch, [_item(1), _item(2)])
    _scan_with(monkeypatch, [_item(1)], complete=False)
    db = SessionLocal()
    got = _statuses(db)
    db.close()
    assert got[f"{LOT_PREFIX}2"] == "OPEN", "closed a lot on a partial read"


def test_a_truncated_rescan_still_refreshes_what_it_saw(monkeypatch, cleanup):
    """Not closing is not the same as doing nothing: everything in the window
    is confirmed live and gets its current ask."""
    _scan_with(monkeypatch, [_item(1, price=20.0)])
    _scan_with(monkeypatch, [_item(1, price=12.0)], complete=False)
    db = SessionLocal()
    lot = db.query(models.Lot).filter(
        models.Lot.lot_id == f"{LOT_PREFIX}1").first()
    price = float(lot.current_bid)
    db.close()
    assert price == 12.0


def test_the_scan_reports_what_it_covered(monkeypatch, cleanup):
    """So the UI can say "read 2 of 10 pages, nothing closed" rather than
    implying the inventory is now accurate."""
    card = _scan_with(monkeypatch, [_item(1)], complete=False)[0]
    assert card["scan_complete"] is False
    assert card["scan_total_pages"] == 10
    card = _scan_with(monkeypatch, [_item(1)], complete=True)[0]
    assert card["scan_complete"] is True
