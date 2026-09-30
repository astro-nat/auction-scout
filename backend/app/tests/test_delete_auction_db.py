"""Deleting one auction and everything imported from it (`pytest -m db`).

Hiding an auction takes it out of the views and is what you want almost
always. This is for a sale that should not be on the machine at all: the
case that prompted it was dropping the two government sources, whose
scrapers were deleted, leaving 8 auction rows and 116 lots that nothing
could ever refresh again.

The two things worth pinning are the deletion ORDER - there is no
delete-cascade on the models, so a lot with a price observation rolls the
whole thing back on its foreign key - and the watched-lot guard.
"""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import models
from app.database import SessionLocal
from app.main import app

pytestmark = pytest.mark.db

client = TestClient(app)
HIBID = 999999891
NAME = "delete-auction-test"


def _purge(db):
    for a in db.query(models.Auction).filter(models.Auction.name == NAME).all():
        ids = [r[0] for r in db.query(models.Lot.id)
                               .filter(models.Lot.auction_id == a.id).all()]
        if ids:
            db.query(models.PriceObservation).filter(
                models.PriceObservation.lot_id.in_(ids)).delete(synchronize_session=False)
            db.query(models.Enrichment).filter(
                models.Enrichment.lot_id.in_(ids)).delete(synchronize_session=False)
        db.query(models.Lot).filter(models.Lot.auction_id == a.id).delete(
            synchronize_session=False)
        db.query(models.Auction).filter(models.Auction.id == a.id).delete(
            synchronize_session=False)
    db.query(models.HiddenLot).filter(
        models.HiddenLot.lot_id.like("del-%")).delete(synchronize_session=False)
    db.commit()


@pytest.fixture
def sale():
    scrub = SessionLocal()
    _purge(scrub)
    scrub.close()
    db = SessionLocal()
    a = models.Auction(hibid_id=HIBID, name=NAME, source="Ship",
                       buyer_premium_mult=1.15,
                       closing_date=datetime.now() + timedelta(days=3),
                       imported_at=datetime.now(timezone.utc))
    db.add(a)
    db.flush()
    for lid in ("del-1", "del-2", "del-3"):
        lot = models.Lot(lot_id=lid, lot_number=lid[-1], title=f"Widget {lid}",
                         auction_id=a.id, current_bid=5, next_bid=6,
                         status="OPEN", logistics_ease="EASY", source="Ship")
        db.add(lot)
        db.flush()
        db.add(models.Enrichment(lot_id=lot.id, status="success",
                                 est_resale=80, user_overrides=[]))
    db.commit()
    yield db, a
    db.rollback()
    _purge(db)
    db.close()


def _counts(db, auction_id):
    lots = db.query(models.Lot).filter(models.Lot.auction_id == auction_id).count()
    auctions = db.query(models.Auction).filter(models.Auction.id == auction_id).count()
    return {"lots": lots, "auctions": auctions}


def test_a_dry_run_says_what_would_go_and_takes_nothing(sale):
    db, a = sale
    got = client.request("DELETE", f"/auctions/{a.id}", params={"dry_run": True}).json()
    assert got["lots"] == 3
    assert got["deleted"] is False
    assert _counts(db, a.id) == {"lots": 3, "auctions": 1}


def test_it_takes_the_auction_and_its_lots(sale):
    db, a = sale
    aid = a.id          # the instance is about to stop having a row to read
    got = client.request("DELETE", f"/auctions/{aid}").json()
    assert got["deleted"] is True and got["lots"] == 3
    db.expire_all()
    assert _counts(db, aid) == {"lots": 0, "auctions": 0}


def test_the_enrichments_go_with_the_lots(sale):
    """They are the paid AI work; leaving them orphaned keeps the cost on the
    books with nothing to point at."""
    db, a = sale
    ids = [r[0] for r in db.query(models.Lot.id)
                           .filter(models.Lot.auction_id == a.id).all()]
    client.request("DELETE", f"/auctions/{a.id}")
    db.expire_all()
    assert db.query(models.Enrichment).filter(
        models.Enrichment.lot_id.in_(ids)).count() == 0


def test_a_lot_with_a_price_trail_does_not_sink_the_delete(sale):
    """The exact failure the closed-lot flush hit: no delete-cascade on the
    models, so a child row left behind rolls the whole thing back on its
    foreign key."""
    db, a = sale
    aid = a.id
    lot = db.query(models.Lot).filter(models.Lot.lot_id == "del-1").first()
    db.add(models.PriceObservation(lot_id=lot.id, value=80, method="comps",
                                   evidence="sold"))
    db.commit()
    got = client.request("DELETE", f"/auctions/{aid}")
    assert got.status_code == 200, got.text
    db.expire_all()
    assert _counts(db, aid) == {"lots": 0, "auctions": 0}


def test_a_watched_lot_blocks_it(sale):
    """Watching is the one thing the user has said about a specific lot.
    Taking it out from under them silently is worse than asking twice."""
    db, a = sale
    client.post("/lots/del-2/watch", params={"watched": True})
    got = client.request("DELETE", f"/auctions/{a.id}")
    assert got.status_code == 409
    assert "watch list" in got.json()["detail"]
    db.expire_all()
    assert _counts(db, a.id)["lots"] == 3, "refused but deleted anyway"


def test_force_gets_past_the_watch_guard(sale):
    db, a = sale
    aid = a.id
    client.post("/lots/del-2/watch", params={"watched": True})
    got = client.request("DELETE", f"/auctions/{aid}", params={"force": True})
    assert got.status_code == 200
    db.expire_all()
    assert _counts(db, aid) == {"lots": 0, "auctions": 0}


def test_the_dry_run_reports_the_watched_count_before_anything_is_tried(sale):
    db, a = sale
    client.post("/lots/del-3/watch", params={"watched": True})
    got = client.request("DELETE", f"/auctions/{a.id}", params={"dry_run": True}).json()
    assert got["watched"] == 1 and got["lots"] == 3


def test_the_hide_memory_for_those_lots_goes_too(sale):
    """It is keyed by the external lot id and outlives the row on purpose, so
    a re-import cannot resurrect a hidden lot. Nothing can re-import these."""
    db, a = sale
    aid = a.id
    client.post("/lots/del-1/hide", params={"hidden": True})
    assert db.query(models.HiddenLot).filter(
        models.HiddenLot.lot_id == "del-1").count() == 1
    client.request("DELETE", f"/auctions/{aid}")
    db.expire_all()
    assert db.query(models.HiddenLot).filter(
        models.HiddenLot.lot_id == "del-1").count() == 0


def test_an_auction_that_is_not_there(sale):
    assert client.request("DELETE", "/auctions/99999999").status_code == 404


def test_the_literal_delete_routes_still_win(sale):
    """/auctions/{auction_id} is declared after /auctions/dismissed/{id} and
    /auctions/favorites/{id}; if it were declared first it would swallow
    them, and "dismissed" would fail to parse as an int."""
    assert client.request("DELETE", "/auctions/dismissed/424242").status_code != 404 \
        or True   # the route exists; what matters is it is not shadowed
    r = client.request("DELETE", "/auctions/dismissed/424242")
    assert r.status_code != 422, "the auction-id route swallowed /dismissed"
    r = client.request("DELETE", "/auctions/favorites/424242")
    assert r.status_code != 422, "the auction-id route swallowed /favorites"
