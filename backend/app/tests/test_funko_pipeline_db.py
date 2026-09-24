"""The Funko guards inside the pipeline (`pytest -m db`).

test_funko_guards pins the rules. This pins what enrichment and re-price
do with them: what gets searched, what is written on the row, and what can
still earn the gold badge.
"""

from datetime import datetime, timezone

import pytest

from app import models
from app.database import SessionLocal
from app.services import pricing
from app.workers import enrich

pytestmark = pytest.mark.db

TEST_HIBID = 999999975
FOUND = {"est_resale": 200, "price_low": 180, "price_high": 220,
         "comp_count": 8, "price_source": "sold (SoldComps)", "comps": []}


@pytest.fixture
def make(monkeypatch):
    """make(listing, ai_title) -> (db, lot_id, searched). The AI title is
    what the vision pass would return; comps come back at $200 for anything."""
    searched = []
    state = {"ai": None}
    monkeypatch.setattr(enrich, "_call_text", lambda *a, **k: {
        "enriched_title": state["ai"], "verdict": "normal wear and tear",
        "confident": True, "notes": "n", "ship": "EASY"})
    monkeypatch.setattr(enrich, "_download_image", lambda *a, **k: None)
    monkeypatch.setattr(enrich, "_verify_gold", lambda *a, **k: None)
    monkeypatch.setattr(pricing, "verified_title_price", lambda *a, **k: None)
    monkeypatch.setattr(pricing, "lookup_comps",
                        lambda t: (searched.append(t), dict(FOUND))[1])
    db = SessionLocal()
    auction = models.Auction(hibid_id=TEST_HIBID, name="funko-pipeline-test",
                             buyer_premium_mult=1.15, source="Ship",
                             imported_at=datetime.now(timezone.utc))
    db.add(auction)
    db.flush()
    made = []

    def _make(listing, ai_title):
        state["ai"] = ai_title
        lot = models.Lot(lot_id=f"fk-{len(made)}", title=listing, description="x" * 200,
                         auction_id=auction.id, current_bid=1, next_bid=2,
                         logistics_ease="EASY", source="Ship")
        db.add(lot)
        db.flush()
        # Stamped as claimed so the dev worker beside the suite never takes it.
        db.add(models.Enrichment(lot_id=lot.id, status="queued", queued_task="enrich",
                                 claimed_at=datetime.now(timezone.utc), user_overrides=[]))
        db.commit()
        made.append(lot.id)
        return lot.id

    yield db, _make, searched
    db.rollback()
    db.query(models.PriceObservation).filter(
        models.PriceObservation.lot_id.in_(made)).delete(synchronize_session=False)
    db.query(models.TaskTiming).filter(
        models.TaskTiming.auction_id == auction.id).delete(synchronize_session=False)
    db.query(models.Enrichment).filter(
        models.Enrichment.lot_id.in_(made)).delete(synchronize_session=False)
    db.query(models.Lot).filter(models.Lot.id.in_(made)).delete(synchronize_session=False)
    db.query(models.Auction).filter(models.Auction.id == auction.id).delete(
        synchronize_session=False)
    db.commit()
    db.close()


def _e(db, lot_id):
    db.expire_all()
    return db.query(models.Enrichment).filter(models.Enrichment.lot_id == lot_id).one()


def test_an_unauthenticated_signature_is_priced_as_the_plain_pop(make):
    db, new, searched = make
    lot_id = new("Pedro Martinez HOF Autographed Funko POP! #55",
                 "Pedro Martinez Autographed Funko Pop #55 Red Sox")
    enrich.run_enrichment(lot_id)
    e = _e(db, lot_id)
    assert e.status == "success", e.error_message
    assert "autograph" not in searched[-1].lower()
    assert "not authenticated" in e.fraud_note


def test_a_custom_pop_can_never_be_gold(make):
    db, new, _ = make
    lot_id = new("Custom Funko Pop Walter White #1", "Custom Funko Pop Walter White")
    enrich.run_enrichment(lot_id)
    e = _e(db, lot_id)
    assert e.roi_status == "PASS"
    assert "fraud check" in e.roi_reason and "custom" in e.roi_reason.lower()


def test_a_named_authenticator_keeps_the_signature_and_asks_for_the_cert(make):
    db, new, searched = make
    lot_id = new("Chevy Chase Signed Funko Pop #242 JSA COA",
                 "Chevy Chase Signed Funko Pop #242 Christmas Vacation JSA")
    enrich.run_enrichment(lot_id)
    e = _e(db, lot_id)
    assert "Signed" in searched[-1]
    assert e.auth_required is True
    assert "verify the cert" in e.fraud_note


def test_the_reprice_path_applies_the_same_guards(make, monkeypatch):
    db, new, searched = make
    lot_id = new("Pedro Martinez HOF Autographed Funko POP! #55", None)
    e = _e(db, lot_id)
    e.status, e.enriched_title, e.est_resale = "success", \
        "Pedro Martinez Autographed Funko Pop #55", 75
    db.commit()
    for name, fn in (("start", lambda *a, **k: "test-fk-job"), ("get", lambda *a, **k: {}),
                     ("is_cancelled", lambda *a, **k: False),
                     ("update", lambda *a, **k: None), ("finish", lambda *a, **k: None)):
        monkeypatch.setattr(enrich.jobs, name, fn)
    monkeypatch.setattr(pricing, "retail_from_title", lambda *a, **k: None)
    searched.clear()
    enrich.run_reprice([lot_id])
    assert searched and "autograph" not in searched[-1].lower()
    assert "not authenticated" in _e(db, lot_id).fraud_note
