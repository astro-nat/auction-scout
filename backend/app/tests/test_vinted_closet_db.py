"""A Vinted seller's whole closet (`pytest -m db`).

A keyword scan only ever finds the slice of someone's closet that matched
the words. The member page renders its items in the browser, so there is
nothing to scrape there; it calls /api/v2/wardrobe/<id>/items, and so does
fetch_wardrobe. Here the fetch is stubbed - what is pinned is what the
import does with it.
"""

from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from app import models
from app.database import SessionLocal
from app.main import app
from app.services import vinted

pytestmark = pytest.mark.db

client = TestClient(app)
SELLER = 999999123


def _item(n, price=18.0):
    return {"item_id": 90000000 + n, "lot_id": f"vt-{90000000 + n}",
            "title": f"Vintage Pyrex Dish {n}", "brand": "Pyrex",
            "condition": "Good", "price": price,
            "thumbnail_url": f"https://images1.vinted.net/{n}.webp",
            "lot_link": f"https://www.vinted.com/items/{90000000 + n}-dish",
            "seller_id": SELLER}


@pytest.fixture
def clean():
    yield
    db = SessionLocal()
    try:
        a = (db.query(models.Auction)
               .filter(models.Auction.external_id == f"vt-user-{SELLER}").first())
        if a:
            ids = [l.id for l in db.query(models.Lot).filter(models.Lot.auction_id == a.id)]
            db.query(models.Enrichment).filter(
                models.Enrichment.lot_id.in_(ids)).delete(synchronize_session=False)
            db.query(models.Lot).filter(models.Lot.id.in_(ids)).delete(
                synchronize_session=False)
            db.query(models.Auction).filter(models.Auction.id == a.id).delete(
                synchronize_session=False)
            db.commit()
    finally:
        db.close()


def _import(monkeypatch, items, login="jace91004"):
    monkeypatch.setattr(vinted, "fetch_wardrobe",
                        lambda uid, **k: {"seller": {"id": uid, "login": login},
                                          "items": items, "total": len(items)})
    r = client.post(f"/vinted/seller/{SELLER}/import")
    assert r.status_code == 200, r.text
    return r.json()[0]


def test_the_closet_becomes_an_auction_of_its_own(monkeypatch, clean):
    a = _import(monkeypatch, [_item(1), _item(2), _item(3)])
    assert a["name"] == "Vinted closet: jace91004"
    assert a["external_id"] == f"vt-user-{SELLER}"
    assert a["lot_count"] == 3
    assert a["source_url"] == f"https://www.vinted.com/member/{SELLER}"

    db = SessionLocal()
    try:
        lots = db.query(models.Lot).filter(models.Lot.auction_id == a["id"]).all()
        assert len(lots) == 3
        assert {l.seller_name for l in lots} == {"jace91004"}
        assert {l.seller_id for l in lots} == {str(SELLER)}
        assert all(l.enrichment is not None for l in lots)   # queued for pricing
    finally:
        db.close()


def test_running_it_again_refreshes_and_closes_what_is_gone(monkeypatch, clean):
    """Re-running is the watch: the ask refreshes, and an item no longer in
    the closet is sold or delisted either way."""
    _import(monkeypatch, [_item(1), _item(2)])
    a = _import(monkeypatch, [_item(1, price=12.0)])
    assert a["lot_count"] == 1

    db = SessionLocal()
    try:
        rows = {l.lot_id: l for l in
                db.query(models.Lot).filter(models.Lot.auction_id == a["id"]).all()}
        assert float(rows["vt-90000001"].current_bid) == 12.0
        assert rows["vt-90000001"].status == "OPEN"
        assert rows["vt-90000002"].status == "CLOSED"
    finally:
        db.close()


def test_a_seller_with_no_name_is_still_importable(monkeypatch, clean):
    a = _import(monkeypatch, [_item(1)], login=None)
    assert a["name"] == f"Vinted closet: {SELLER}"


def test_a_vinted_failure_is_reported_not_swallowed(monkeypatch, clean):
    def boom(*a, **k):
        raise RuntimeError("401 from the wardrobe API")
    monkeypatch.setattr(vinted, "fetch_wardrobe", boom)
    r = client.post(f"/vinted/seller/{SELLER}/import")
    assert r.status_code == 502
    assert "wardrobe" in r.json()["detail"]
