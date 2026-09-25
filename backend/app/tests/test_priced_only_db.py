"""The Priced inventory tab's filter (`pytest -m db`).

`priced_only` on /lots and /lots/count means "the app has a value for it",
whatever tier the value came from. The list and the count share one filter
function so the tab's label and its rows can never disagree.
"""

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app import models
from app.database import SessionLocal
from app.main import app

pytestmark = pytest.mark.db

client = TestClient(app)
TEST_HIBID = 999999983


@pytest.fixture
def auction():
    db = SessionLocal()
    a = models.Auction(hibid_id=TEST_HIBID, name="priced-only-test",
                       imported_at=datetime.now(timezone.utc))
    db.add(a)
    db.flush()
    rows = [
        models.Lot(lot_id="po-priced", title="Priced", auction_id=a.id, current_bid=1),
        models.Lot(lot_id="po-unpriced", title="Unpriced", auction_id=a.id, current_bid=1),
        models.Lot(lot_id="po-pending", title="Never looked at", auction_id=a.id, current_bid=1),
    ]
    db.add_all(rows)
    db.flush()
    db.add(models.Enrichment(lot_id=rows[0].id, status="success", est_resale=40))
    db.add(models.Enrichment(lot_id=rows[1].id, status="success", est_resale=None))
    db.commit()
    ids = [r.id for r in rows]
    yield a.id
    db.rollback()
    db.query(models.Enrichment).filter(models.Enrichment.lot_id.in_(ids)).delete(
        synchronize_session=False)
    db.query(models.Lot).filter(models.Lot.id.in_(ids)).delete(synchronize_session=False)
    db.query(models.Auction).filter(models.Auction.id == a.id).delete(
        synchronize_session=False)
    db.commit()
    db.close()


def test_priced_only_keeps_just_the_lots_with_a_value(auction):
    r = client.get("/lots", params={"auction_id": auction, "priced_only": "true"})
    assert r.status_code == 200, r.text
    assert [l["lot_id"] for l in r.json()] == ["po-priced"]


def test_the_count_agrees_with_the_list(auction):
    both = client.get("/lots/count", params={"auction_id": auction}).json()["total"]
    priced = client.get("/lots/count", params={"auction_id": auction,
                                               "priced_only": "true"}).json()["total"]
    assert (both, priced) == (3, 1)


def test_without_the_flag_nothing_changes(auction):
    r = client.get("/lots", params={"auction_id": auction})
    assert sorted(l["lot_id"] for l in r.json()) == ["po-pending", "po-priced", "po-unpriced"]


def _set_comps(value):
    db = SessionLocal()
    try:
        e = (db.query(models.Enrichment)
               .join(models.Lot, models.Lot.id == models.Enrichment.lot_id)
               .filter(models.Lot.lot_id == "po-priced").one())
        e.comps = value
        e.comp_count = len(value or [])
        db.commit()
    finally:
        db.close()


def _stored_comps():
    db = SessionLocal()
    try:
        return (db.query(models.Enrichment)
                  .join(models.Lot, models.Lot.id == models.Enrichment.lot_id)
                  .filter(models.Lot.lot_id == "po-priced").one()).comps
    finally:
        db.close()


COMP = [{"kind": "sold", "price": 40.0, "title": "a real comp"}]


def test_the_list_leaves_comp_records_behind_but_one_lot_still_carries_them(auction):
    """The comp records were 60% of the list payload - 2.8 MB a page - and
    are read only when one row's evidence panel is opened, which fetches
    that lot on its own."""
    _set_comps(COMP)
    listed = client.get("/lots", params={"auction_id": auction}).json()
    priced = next(l for l in listed if l["lot_id"] == "po-priced")
    assert priced["enrichment"]["comps"] is None
    # The count still rides along, so a row can say how many it will fetch.
    assert priced["enrichment"]["comp_count"] == len(COMP)

    with_comps = client.get("/lots", params={"auction_id": auction,
                                             "include_comps": "true"}).json()
    asked = next(l for l in with_comps if l["lot_id"] == "po-priced")
    assert asked["enrichment"]["comps"][0]["title"] == "a real comp"

    one = client.get("/lots/po-priced").json()
    assert one["enrichment"]["comps"][0]["title"] == "a real comp"


def test_clearing_the_comps_for_the_response_never_reaches_the_database(auction):
    """The whole risk of the optimisation: the rows come out of the session,
    so blanking a field on them must not be written back."""
    _set_comps(COMP)
    for _ in range(3):
        client.get("/lots", params={"auction_id": auction})
    assert _stored_comps() == COMP
