"""The guard against invented identifiers, end to end (`pytest -m db`).

test_invented_identifiers pins the detector. This pins what the pipeline
does with a hit: the comp search uses the listing's own title, the claim
is recorded on the row, the gold badge is withheld with a reason that
names the claim, and a title the user set by hand is never second-guessed
- typing "Denon DRM-555" from the badge in the photo is the correction the
guard exists to prompt, not an invention to flag.
"""

from datetime import datetime, timezone

import pytest

from app import models
from app.database import SessionLocal
from app.services import pricing
from app.workers import enrich

pytestmark = pytest.mark.db

TEST_HIBID = 999999984
LISTING = "CASSETTE TAPE DECK ~ JN-6-23 (R48B)"
INVENTED = "Vintage Denon DR-M11 Stereo Cassette Tape Deck Rack Mount Black"
FAITHFUL = "Cassette Tape Deck JN-6-23 Rack Mount"
FOUND = {"est_resale": 60, "price_low": 50, "price_high": 70,
         "comp_count": 6, "price_source": "sold (SoldComps)", "comps": []}


@pytest.fixture
def lot(monkeypatch):
    """A queued lot; the AI title is set per test through `ai_title`."""
    state = {"title": INVENTED, "searched": []}
    monkeypatch.setattr(enrich, "_call_text", lambda *a, **k: {
        "enriched_title": state["title"], "verdict": "normal wear and tear",
        "confident": True, "notes": "n", "ship": "EASY"})
    monkeypatch.setattr(enrich, "_download_image", lambda *a, **k: None)
    monkeypatch.setattr(enrich, "_verify_gold", lambda *a, **k: None)
    monkeypatch.setattr(pricing, "verified_title_price", lambda *a, **k: None)
    monkeypatch.setattr(pricing, "lookup_comps",
                        lambda t: (state["searched"].append(t), dict(FOUND))[1])

    db = SessionLocal()
    auction = models.Auction(hibid_id=TEST_HIBID, name="identity-guard-test",
                             buyer_premium_mult=1.15, source="Ship",
                             imported_at=datetime.now(timezone.utc))
    db.add(auction)
    db.flush()
    row = models.Lot(lot_id="ig-1", title=LISTING, description="x" * 200,
                     auction_id=auction.id, current_bid=1, next_bid=2,
                     logistics_ease="EASY", source="Ship")
    db.add(row)
    db.flush()
    db.add(models.Enrichment(lot_id=row.id, status="queued", queued_task="enrich",
                             user_overrides=[]))
    db.commit()
    lot_id = row.id
    yield db, lot_id, state
    db.rollback()
    db.query(models.PriceObservation).filter(
        models.PriceObservation.lot_id == lot_id).delete(synchronize_session=False)
    db.query(models.TaskTiming).filter(
        models.TaskTiming.auction_id == auction.id).delete(synchronize_session=False)
    db.query(models.Enrichment).filter(
        models.Enrichment.lot_id == lot_id).delete(synchronize_session=False)
    db.query(models.Lot).filter(models.Lot.id == lot_id).delete(synchronize_session=False)
    db.query(models.Auction).filter(models.Auction.id == auction.id).delete(
        synchronize_session=False)
    db.commit()
    db.close()


def _e(db, lot_id):
    db.expire_all()
    return db.query(models.Enrichment).filter(models.Enrichment.lot_id == lot_id).one()


def test_an_invented_model_number_means_the_listing_title_is_searched(lot):
    """The Denon: the AI said DR-M11, the listing never did. Search the
    listing's words, record the claim, withhold the badge and say why."""
    db, lot_id, state = lot
    enrich.run_enrichment(lot_id)
    e = _e(db, lot_id)
    assert e.status == "success", e.error_message
    assert state["searched"] == [LISTING]
    assert e.identity_note == "DR-M11"
    assert e.enriched_title == INVENTED           # the claim stays visible
    assert float(e.est_resale) == pytest.approx(60.0)
    assert e.roi_status == "PASS"
    assert "identity uncertain" in e.roi_reason and "DR-M11" in e.roi_reason


def test_a_faithful_title_is_searched_and_leaves_no_note(lot):
    db, lot_id, state = lot
    state["title"] = FAITHFUL
    enrich.run_enrichment(lot_id)
    e = _e(db, lot_id)
    assert state["searched"] == [FAITHFUL]
    assert e.identity_note is None
    assert "identity" not in (e.roi_reason or "")


def test_a_hand_set_title_is_never_second_guessed(lot):
    """The user typed the model from the badge. That IS the correction."""
    db, lot_id, state = lot
    e = _e(db, lot_id)
    e.enriched_title = "Denon DRM-555 Auto Reverse Rack Mount Cassette Deck"
    e.user_overrides = ["enriched_title"]
    db.commit()
    enrich.run_enrichment(lot_id)
    e = _e(db, lot_id)
    assert e.identity_note is None
    assert state["searched"] == ["Denon DRM-555 Auto Reverse Rack Mount Cassette Deck"]
    assert e.roi_status != "PASS" or "identity" not in (e.roi_reason or "")


def test_the_reprice_path_applies_the_same_guard(lot, monkeypatch):
    """A lot enriched before the guard existed is caught on its next
    re-price: the stored AI title still says DR-M11."""
    db, lot_id, state = lot
    e = _e(db, lot_id)
    e.status, e.enriched_title, e.est_resale = "success", INVENTED, 105
    e.price_source, e.comp_count = "sold (SoldComps)", 31
    db.commit()
    monkeypatch.setattr(enrich.jobs, "start", lambda *a, **k: "test-ig-job")
    monkeypatch.setattr(enrich.jobs, "get", lambda *a, **k: {})
    monkeypatch.setattr(enrich.jobs, "is_cancelled", lambda *a, **k: False)
    monkeypatch.setattr(enrich.jobs, "update", lambda *a, **k: None)
    monkeypatch.setattr(enrich.jobs, "finish", lambda *a, **k: None)
    monkeypatch.setattr(pricing, "retail_from_title", lambda *a, **k: None)
    state["searched"].clear()
    enrich.run_reprice([lot_id])
    e = _e(db, lot_id)
    assert state["searched"] == [LISTING]
    assert e.identity_note == "DR-M11"
    assert e.roi_status == "PASS" and "identity uncertain" in e.roi_reason


def test_the_badge_is_withheld_by_the_note_alone(lot):
    """_apply_roi on its own: a lead that would be gold stays PASS while the
    note stands, and the reason names the claim."""
    db, lot_id, _ = lot
    row = db.query(models.Lot).filter(models.Lot.id == lot_id).one()
    e = row.enrichment
    e.est_resale, e.comp_count, e.price_source = 200, 12, "sold (SoldComps)"
    e.identity_note = "#221"
    enrich._apply_roi(row, e)
    assert e.roi_status == "PASS"
    assert "#221" in e.roi_reason
    e.identity_note = None
    enrich._apply_roi(row, e)
    assert e.roi_status == "GOLD MINE"
