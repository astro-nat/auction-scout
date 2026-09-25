"""Dismissing an auction takes its lots with it (`pytest -m db`).

Hiding a sale used to hide only the sale. Its listings stayed in the items
views and stayed eligible for bulk pricing - 187 Vinted "ssd" lots sat in
the inventory after the scan they came from had been dismissed, which is
not hiding it.

The lots are filtered, never marked, so bringing the auction back brings
them back untouched.
"""

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app import models
from app.database import SessionLocal
from app.main import app

pytestmark = pytest.mark.db

client = TestClient(app)
SHOWN, HIDDEN = 999999941, 999999942


@pytest.fixture
def two_auctions():
    db = SessionLocal()
    made = []
    for hib, hidden, tag in ((SHOWN, False, "shown"), (HIDDEN, True, "hidden")):
        a = models.Auction(hibid_id=hib, name=f"{tag}-auction", source="Ship",
                           buyer_premium_mult=1.15, hidden=hidden,
                           imported_at=datetime.now(timezone.utc))
        db.add(a)
        db.flush()
        lot = models.Lot(lot_id=f"ha-{tag}", title=f"Widget {tag}",
                         auction_id=a.id, current_bid=2, next_bid=3,
                         status="OPEN", logistics_ease="EASY", source="Ship")
        db.add(lot)
        db.flush()
        db.add(models.Enrichment(lot_id=lot.id, status="pending",
                                 user_overrides=[]))
        made.append((a, lot))
    db.commit()
    yield db, made
    db.rollback()
    for a, lot in made:
        db.query(models.Enrichment).filter(
            models.Enrichment.lot_id == lot.id).delete(synchronize_session=False)
        db.query(models.Lot).filter(models.Lot.id == lot.id).delete(
            synchronize_session=False)
        db.query(models.Auction).filter(models.Auction.id == a.id).delete(
            synchronize_session=False)
    db.commit()
    db.close()


def _ids(**params):
    r = client.get("/lots", params={"limit": 2000, **params})
    assert r.status_code == 200, r.text
    body = r.json()
    lots = body.get("items", body) if isinstance(body, dict) else body
    return {l["lot_id"] for l in lots}


def test_a_dismissed_auctions_lots_leave_the_items_view(two_auctions):
    got = _ids()
    assert "ha-shown" in got
    assert "ha-hidden" not in got


def test_the_count_agrees_with_the_list(two_auctions):
    """They share a filter precisely so they cannot disagree about what is
    in view."""
    before = client.get("/lots/count").json()["total"]
    db, made = two_auctions
    made[1][0].hidden = False
    db.commit()
    after = client.get("/lots/count").json()["total"]
    assert after == before + 1


def test_asking_for_the_auction_by_name_still_reaches_its_lots(two_auctions):
    """The auction-name click is the only way back to a dismissed sale's own
    items, so an explicit auction_id is honoured."""
    db, made = two_auctions
    assert _ids(auction_id=made[1][0].id) == {"ha-hidden"}


def test_bringing_the_auction_back_brings_the_lots_back(two_auctions):
    db, made = two_auctions
    made[1][0].hidden = False
    db.commit()
    assert "ha-hidden" in _ids()


def test_nothing_was_written_onto_the_lot(two_auctions):
    db, made = two_auctions
    _ids()
    db.expire_all()
    lot = db.query(models.Lot).filter(models.Lot.lot_id == "ha-hidden").one()
    assert lot.hidden in (False, None)


def test_a_dismissed_auctions_lots_are_not_worth_spending_on(two_auctions):
    """_worth_pricing gates every bulk path. A dismissed sale's lots must
    not be in it."""
    from app.routers.enrichment import _worth_pricing
    db, made = two_auctions
    ids = {r[0] for r in db.query(models.Lot.lot_id)
                            .filter(_worth_pricing()).all()}
    assert "ha-shown" in ids
    assert "ha-hidden" not in ids
