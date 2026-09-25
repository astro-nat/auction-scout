"""Hiding every copy of a product at once (`pytest -m db`).

Hiding was the most-used action in the app, and 57 of 68 came in bursts -
the same thing dismissed four, six, nine times in a row. A liquidation sale
lists one product many times and tells the copies apart with an asset tag,
so "Used Dell Precision 7750 Laptop (Qty. 1) FXA 92561" has five siblings
that the pricing key deliberately keeps apart.
"""

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app import models
from app.database import SessionLocal
from app.main import app

pytestmark = pytest.mark.db

client = TestClient(app)
TEST_HIBID = 999999931

# Invented brands, not Dell and Drieaz: the route matches across the whole
# table on purpose, and the shared dev database already holds rows with the
# real titles. The shapes are the real ones - a model number mid-title, an
# asset tag at the end, a two-word product name, a bare category.
TITLES = [
    ("hl-1", "Used Zorbix Precision 7750 Laptop (Qty. 1) FXA 92561"),
    ("hl-2", "Used Zorbix Precision 7750 Laptop (Qty. 1) FXA 92608"),
    ("hl-3", "Used Zorbix Precision 7750 Laptop (Qty. 1) FXA 92603"),
    ("hl-4", "Used Zorbix Precision 7760 Laptop (Qty. 1) FXA 93228"),
    ("hl-5", "Quibblesnort Humidifier ~ IA-25158"),
    ("hl-6", "Quibblesnort Humidifier ~ IA-25157"),
    ("hl-7", "Pyrex"),
]


@pytest.fixture
def seeded():
    db = SessionLocal()
    a = models.Auction(hibid_id=TEST_HIBID, name="hide-like-test", source="Ship",
                       buyer_premium_mult=1.15,
                       imported_at=datetime.now(timezone.utc))
    db.add(a)
    db.flush()
    for lid, title in TITLES:
        db.add(models.Lot(lot_id=lid, title=title, auction_id=a.id,
                          current_bid=5, next_bid=6, status="OPEN",
                          logistics_ease="EASY", source="Ship", hidden=False))
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


def _hidden(db, a):
    db.expire_all()
    return {r.lot_id for r in db.query(models.Lot)
                                .filter(models.Lot.auction_id == a.id,
                                        models.Lot.hidden.is_(True)).all()}


def _post(lot_id, **params):
    r = client.post(f"/lots/{lot_id}/hide-like", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def test_a_dry_run_counts_without_changing_anything(seeded):
    db, a = seeded
    body = _post("hl-1", dry_run=True)
    assert body["changed"] == 3
    assert body["matched"] == 3
    assert len(body["titles"]) == 3
    assert _hidden(db, a) == set()


def test_it_hides_the_asset_tagged_siblings(seeded):
    db, a = seeded
    assert _post("hl-1")["changed"] == 3
    assert _hidden(db, a) == {"hl-1", "hl-2", "hl-3"}


def test_a_different_model_number_is_a_different_product(seeded):
    """7750 and 7760 are not the same laptop. The tag is dropped; a model
    number in the middle of the title never is."""
    db, a = seeded
    _post("hl-1")
    assert "hl-4" not in _hidden(db, a)


def test_a_two_word_product_name_still_groups(seeded):
    """The pricing key needs three words and returns None here. Hiding is
    confirmed and reversible, so two is enough."""
    db, a = seeded
    assert _post("hl-5")["changed"] == 2
    assert _hidden(db, a) == {"hl-5", "hl-6"}


def test_a_title_too_generic_to_match_says_so(seeded):
    db, a = seeded
    body = _post("hl-7")
    assert body["key"] is None
    assert body["changed"] == 0
    assert "too generic" in body["reason"]
    assert _hidden(db, a) == set()


def test_running_it_twice_changes_nothing_the_second_time(seeded):
    db, a = seeded
    assert _post("hl-1")["changed"] == 3
    assert _post("hl-1")["changed"] == 0


def test_it_brings_them_all_back(seeded):
    db, a = seeded
    _post("hl-1")
    assert _post("hl-1", hidden=False)["changed"] == 3
    assert _hidden(db, a) == set()


def test_an_unknown_lot_is_a_404(seeded):
    assert client.post("/lots/nope-000/hide-like").status_code == 404
