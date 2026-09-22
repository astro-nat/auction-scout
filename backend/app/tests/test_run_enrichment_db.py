"""The enrichment entry point actually runs (`pytest -m db`).

_enrich had tests. run_enrichment - the function the worker calls, which
opens the session, wraps the lot in a timing phase and writes the final
status - had none. So a missing import in exactly that wrapper broke every
enrichment for a day while the whole suite stayed green, and the only
symptom the app showed was offering to price the same 97 lots again.
"""

from datetime import datetime, timezone

import pytest

from app import models
from app.database import SessionLocal
from app.services import pricing
from app.workers import enrich

pytestmark = pytest.mark.db

TEST_HIBID = 999999973


@pytest.fixture
def queued_lot(monkeypatch):
    """One lot queued for pricing, with every paid call stubbed out."""
    monkeypatch.setattr(enrich, "_call_text", lambda *a, **k: {
        "enriched_title": "Vintage Brass Candlestick",
        "verdict": "normal wear and tear", "confident": True,
        "notes": "n", "ship": "EASY"})
    monkeypatch.setattr(enrich, "_download_image", lambda *a, **k: None)
    monkeypatch.setattr(pricing, "lookup_comps", lambda *a, **k: {
        "est_resale": 60, "price_low": 50, "price_high": 70,
        "comp_count": 6, "price_source": "sold (SoldComps)"})
    monkeypatch.setattr(enrich, "_verify_gold", lambda *a, **k: None)

    db = SessionLocal()
    auction = models.Auction(hibid_id=TEST_HIBID, name="run-enrich-test",
                             buyer_premium_mult=1.15, source="Ship",
                             imported_at=datetime.now(timezone.utc))
    db.add(auction)
    db.flush()
    lot = models.Lot(lot_id="re-1", title="Vintage Brass Candlestick Pair",
                     description="x" * 200, auction_id=auction.id,
                     current_bid=2, next_bid=3, logistics_ease="EASY",
                     source="Ship")
    db.add(lot)
    db.flush()
    db.add(models.Enrichment(lot_id=lot.id, status="queued",
                             queued_task="enrich", user_overrides=[]))
    db.commit()
    lot_id = lot.id
    yield db, lot_id
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


def test_a_queued_lot_comes_out_priced(queued_lot):
    """The end-to-end shape: queued in, success and a value out."""
    db, lot_id = queued_lot
    enrich.run_enrichment(lot_id)
    e = _enrichment(db, lot_id)
    assert e.status == "success", f"failed with: {e.error_message}"
    assert e.est_resale is not None


def test_a_crash_is_recorded_rather_than_swallowed(queued_lot, monkeypatch):
    """The failure mode that hid this: the lot goes back to 'failed', which
    makes it eligible again, so the app cheerfully re-offers it forever. The
    message has to say what actually went wrong."""
    db, lot_id = queued_lot

    def boom(*a, **k):
        raise NameError("name 'timed' is not defined")
    monkeypatch.setattr(enrich, "_enrich", boom)

    enrich.run_enrichment(lot_id)
    e = _enrichment(db, lot_id)
    assert e.status == "failed"
    assert "timed" in (e.error_message or "")


def test_the_lot_is_timed(queued_lot):
    """The timing wrapper is where the missing import lived, so assert it
    runs rather than trusting that it imports."""
    db, lot_id = queued_lot
    enrich.run_enrichment(lot_id)
    rows = (db.query(models.TaskTiming)
              .filter(models.TaskTiming.kind == "enrich").all())
    assert rows, "no timing row written for the lot"


def test_a_lot_that_is_not_queued_is_left_alone(queued_lot):
    """Cancelling flips queued lots back to pending; anything not still
    queued was cancelled before its turn came up."""
    db, lot_id = queued_lot
    e = _enrichment(db, lot_id)
    e.status = "pending"
    db.commit()
    enrich.run_enrichment(lot_id)
    assert _enrichment(db, lot_id).status == "pending"
