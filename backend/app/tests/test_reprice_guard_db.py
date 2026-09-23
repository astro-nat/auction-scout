"""run_reprice must not erase a value on an empty lookup (`pytest -m db`).

The same guard that went into _enrich this morning, on the other path
that assigns est_resale. run_reprice reuses the stored AI title and
recomputes comps for a list of lots - it is the tool for recovering the
1,390 asking-priced lots - and it had the same unconditional assignment.
Pointing it at 1,390 lots during any future outage would have erased
every value it touched, in bulk, with nothing logged.
"""

from datetime import datetime, timezone

import pytest

from app import models
from app.database import SessionLocal
from app.services import pricing
from app.workers import enrich

pytestmark = pytest.mark.db

TEST_HIBID = 999999977
EMPTY = {"est_resale": None, "price_low": None, "price_high": None,
         "comp_count": 0, "price_source": None, "comps": []}
FOUND = {"est_resale": 60, "price_low": 50, "price_high": 70,
         "comp_count": 6, "price_source": "sold (SoldComps)", "comps": []}


@pytest.fixture
def priced_lot(monkeypatch):
    """A lot carrying $19.49 from asking listings, with the job plumbing
    and every paid call stubbed so only the comps path is real."""
    monkeypatch.setattr(enrich.jobs, "start", lambda *a, **k: "test-reprice-job")
    monkeypatch.setattr(enrich.jobs, "get", lambda *a, **k: {})
    monkeypatch.setattr(enrich.jobs, "is_cancelled", lambda *a, **k: False)
    monkeypatch.setattr(enrich.jobs, "update", lambda *a, **k: None)
    monkeypatch.setattr(enrich.jobs, "finish", lambda *a, **k: None)
    monkeypatch.setattr(enrich, "_verify_gold", lambda *a, **k: None)
    monkeypatch.setattr(pricing, "retail_from_title", lambda *a, **k: None)

    db = SessionLocal()
    auction = models.Auction(hibid_id=TEST_HIBID, name="reprice-guard-test",
                             buyer_premium_mult=1.15, source="Ship",
                             imported_at=datetime.now(timezone.utc))
    db.add(auction)
    db.flush()
    lot = models.Lot(lot_id="rg-1", title="DeWalt DCF825 18V Cordless Impact Driver",
                     auction_id=auction.id, current_bid=2, next_bid=3,
                     logistics_ease="EASY", source="Ship")
    db.add(lot)
    db.flush()
    db.add(models.Enrichment(
        lot_id=lot.id, status="success", user_overrides=[],
        enriched_title="DeWalt DCF825 18V Cordless Impact Driver",
        est_resale=19.49, price_low=15, price_high=25, comp_count=1,
        price_source="active (eBay)"))
    db.commit()
    lot_id = lot.id
    yield db, lot_id
    db.rollback()
    db.query(models.PriceObservation).filter(
        models.PriceObservation.lot_id == lot_id).delete(synchronize_session=False)
    db.query(models.Enrichment).filter(
        models.Enrichment.lot_id == lot_id).delete(synchronize_session=False)
    db.query(models.Lot).filter(models.Lot.id == lot_id).delete(
        synchronize_session=False)
    db.query(models.Auction).filter(models.Auction.id == auction.id).delete(
        synchronize_session=False)
    db.commit()
    db.close()


def _enrichment(db, lot_id):
    db.expire_all()
    return db.query(models.Enrichment).filter(
        models.Enrichment.lot_id == lot_id).one()


def test_an_empty_lookup_keeps_the_value_and_leaves_a_trace(priced_lot, monkeypatch):
    db, lot_id = priced_lot
    monkeypatch.setattr(pricing, "lookup_comps", lambda *a, **k: dict(EMPTY))
    enrich.run_reprice([lot_id])
    e = _enrichment(db, lot_id)
    assert float(e.est_resale) == pytest.approx(19.49)
    assert e.price_source == "active (eBay)"
    empties = [r for r in db.query(models.PriceObservation)
                 .filter(models.PriceObservation.lot_id == lot_id).all()
               if r.method == "empty"]
    assert len(empties) == 1
    assert "reprice lookup returned nothing; kept $19.49" in (empties[0].note or "")


def test_a_real_answer_still_replaces_the_value(priced_lot, monkeypatch):
    """The guard must not make asking prices sticky - recovering them is
    the whole point of the bulk re-price."""
    db, lot_id = priced_lot
    monkeypatch.setattr(pricing, "lookup_comps", lambda *a, **k: dict(FOUND))
    enrich.run_reprice([lot_id])
    e = _enrichment(db, lot_id)
    assert float(e.est_resale) == pytest.approx(60.0)
    assert e.price_source.startswith("sold (SoldComps)")
    methods = {r.method for r in db.query(models.PriceObservation)
               .filter(models.PriceObservation.lot_id == lot_id).all()}
    assert "reprice" in methods and "empty" not in methods


def test_a_hand_corrected_price_is_never_looked_up(priced_lot, monkeypatch):
    """Existing contract, kept: a user override stands, and costs nothing."""
    db, lot_id = priced_lot
    e = _enrichment(db, lot_id)
    e.user_overrides = ["est_resale"]
    e.est_resale = 99
    db.commit()

    def boom(*a, **k):
        raise AssertionError("looked up comps for a hand-corrected lot")
    monkeypatch.setattr(pricing, "lookup_comps", boom)
    enrich.run_reprice([lot_id])
    assert float(_enrichment(db, lot_id).est_resale) == 99
