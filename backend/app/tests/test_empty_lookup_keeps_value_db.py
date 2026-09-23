"""An empty comp lookup must not erase a working estimate (`pytest -m db`).

Every failure path in lookup_comps - no key, breaker open, quota wall,
non-200 - returns the same empty shape as "nothing like this has ever
sold". The comps block used to assign est_resale unconditionally from that
shape, so one re-price during an outage overwrote a working number with
None. Observed on production: a DeWalt DCF825 impact driver went from
$19.49 to no value at all in a single click, and 3,192 lots carry no price
source, some of them almost certainly for this reason.

The guard keeps the previous estimate and records the empty lookup in the
price trail, which is also what finally lets the trail tell "no sold
history" apart from "the API returned nothing".
"""

from datetime import datetime, timezone

import pytest

from app import models
from app.database import SessionLocal
from app.services import pricing
from app.workers import enrich

pytestmark = pytest.mark.db

TEST_HIBID = 999999975

EMPTY = {"est_resale": None, "price_low": None, "price_high": None,
         "comp_count": 0, "price_source": None, "comps": []}
FOUND = {"est_resale": 60, "price_low": 50, "price_high": 70,
         "comp_count": 6, "price_source": "sold (SoldComps)", "comps": []}


@pytest.fixture
def priced_lot(monkeypatch):
    """A lot that already carries a $19.49 estimate, queued for re-price,
    with the AI and audit calls stubbed so only the comps path is real."""
    monkeypatch.setattr(enrich, "_call_text", lambda *a, **k: {
        "enriched_title": "DeWalt DCF825 18V Cordless Impact Driver",
        "verdict": "normal wear and tear", "confident": True,
        "notes": "n", "ship": "EASY"})
    monkeypatch.setattr(enrich, "_download_image", lambda *a, **k: None)
    monkeypatch.setattr(enrich, "_verify_gold", lambda *a, **k: None)
    monkeypatch.setattr(pricing, "verified_title_price", lambda *a, **k: None)

    db = SessionLocal()
    auction = models.Auction(hibid_id=TEST_HIBID, name="empty-lookup-test",
                             buyer_premium_mult=1.15, source="Ship",
                             imported_at=datetime.now(timezone.utc))
    db.add(auction)
    db.flush()
    lot = models.Lot(lot_id="el-1", title="DeWalt DCF825 18V Cordless Impact Driver",
                     description="x" * 200, auction_id=auction.id,
                     current_bid=2, next_bid=3, logistics_ease="EASY",
                     source="Ship")
    db.add(lot)
    db.flush()
    db.add(models.Enrichment(
        lot_id=lot.id, status="queued", queued_task="enrich",
        user_overrides=[], est_resale=19.49, price_low=15, price_high=25,
        comp_count=1, price_source="active (eBay)"))
    db.commit()
    lot_id = lot.id
    yield db, lot_id
    db.rollback()
    db.query(models.PriceObservation).filter(
        models.PriceObservation.lot_id == lot_id).delete(synchronize_session=False)
    db.query(models.TaskTiming).filter(
        models.TaskTiming.auction_id == auction.id).delete(synchronize_session=False)
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


def test_an_empty_lookup_keeps_the_previous_estimate(priced_lot, monkeypatch):
    """The production defect, reproduced: $19.49 in, lookup finds nothing,
    $19.49 must still be there afterwards."""
    db, lot_id = priced_lot
    monkeypatch.setattr(pricing, "lookup_comps", lambda *a, **k: dict(EMPTY))
    enrich.run_enrichment(lot_id)
    e = _enrichment(db, lot_id)
    assert e.status == "success", f"failed with: {e.error_message}"
    assert float(e.est_resale) == pytest.approx(19.49)
    assert e.price_source == "active (eBay)"
    assert e.comp_count == 1


def test_the_empty_lookup_is_recorded_in_the_trail(priced_lot, monkeypatch):
    """The other half: a trace, so an outage is visible after the fact.
    Before this, an empty lookup left nothing behind at all."""
    db, lot_id = priced_lot
    monkeypatch.setattr(pricing, "lookup_comps", lambda *a, **k: dict(EMPTY))
    enrich.run_enrichment(lot_id)
    rows = (db.query(models.PriceObservation)
              .filter(models.PriceObservation.lot_id == lot_id).all())
    empties = [r for r in rows if r.method == "empty"]
    assert len(empties) == 1
    r = empties[0]
    assert r.value is None
    assert r.chosen is False
    assert "kept $19.49" in (r.note or "")
    assert "DCF825" in (r.query or "")


def test_a_successful_lookup_still_replaces_the_estimate(priced_lot, monkeypatch):
    """The guard must not make estimates sticky. Real comps win."""
    db, lot_id = priced_lot
    monkeypatch.setattr(pricing, "lookup_comps", lambda *a, **k: dict(FOUND))
    enrich.run_enrichment(lot_id)
    e = _enrichment(db, lot_id)
    assert float(e.est_resale) == pytest.approx(60.0)   # normal wear = x1.0
    assert e.price_source.startswith("sold (SoldComps)")
    assert e.comp_count == 6


def test_with_no_prior_value_an_empty_lookup_still_yields_none(priced_lot, monkeypatch):
    """Unchanged behaviour: nothing to keep means nothing is kept, and no
    'empty' row is written - there was no value to protect."""
    db, lot_id = priced_lot
    e = _enrichment(db, lot_id)
    e.est_resale = e.price_low = e.price_high = None
    e.comp_count, e.price_source = 0, None
    db.commit()
    monkeypatch.setattr(pricing, "lookup_comps", lambda *a, **k: dict(EMPTY))
    enrich.run_enrichment(lot_id)
    e = _enrichment(db, lot_id)
    assert e.status == "success"
    assert e.est_resale is None
    empties = (db.query(models.PriceObservation)
                 .filter(models.PriceObservation.lot_id == lot_id,
                         models.PriceObservation.method == "empty").count())
    assert empties == 0
