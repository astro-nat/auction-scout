"""Once AI has priced a lot it is locked (`pytest -m db`): no button and no
bulk path pays for AI or a comp lookup on it again. And the row's comps
button prices one lot with no AI at all.

Test rows are stamped claimed where they sit 'queued', so the dev worker
beside the suite never picks one up and spends a real call on it.
"""

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app import models
from app.database import SessionLocal
from app.main import app
from app.services import pricing
from app.workers import enrich

pytestmark = pytest.mark.db

client = TestClient(app)
TEST_HIBID = 999999971
FOUND = {"est_resale": 30, "price_low": 25, "price_high": 35,
         "comp_count": 7, "price_source": "sold (SoldComps)", "comps": []}


@pytest.fixture
def lots():
    db = SessionLocal()
    auction = models.Auction(hibid_id=TEST_HIBID, name="ai-lock-test",
                             buyer_premium_mult=1.15, source="Ship",
                             imported_at=datetime.now(timezone.utc))
    db.add(auction)
    db.flush()
    made = {}
    for key, enr in {
        "ai": dict(status="success", ai_source="vision", est_resale=80,
                   enriched_title="Locked Widget Pro"),
        "blind": dict(status="success", ai_source="none", est_resale=None),
        "comps": dict(status="pending", est_resale=40, price_source="sold (SoldComps)"),
        "bare": dict(status="pending", est_resale=None),
    }.items():
        lot = models.Lot(lot_id=f"lock-{key}", title=f"Lock Test Gadget Model {key}",
                         auction_id=auction.id, current_bid=1, next_bid=2,
                         logistics_ease="EASY", source="Ship",
                         thumbnail_url="https://example.com/x.jpg")
        db.add(lot)
        db.flush()
        db.add(models.Enrichment(lot_id=lot.id, user_overrides=[], **enr))
        made[key] = lot.id
    db.commit()
    yield db, made, auction.id
    db.rollback()
    ids = list(made.values())
    db.query(models.PriceObservation).filter(
        models.PriceObservation.lot_id.in_(ids)).delete(synchronize_session=False)
    db.query(models.Enrichment).filter(
        models.Enrichment.lot_id.in_(ids)).delete(synchronize_session=False)
    db.query(models.Lot).filter(models.Lot.id.in_(ids)).delete(synchronize_session=False)
    db.query(models.Auction).filter(models.Auction.id == auction.id).delete(
        synchronize_session=False)
    db.commit()
    db.close()


def _e(db, lot_id):
    db.expire_all()
    return db.query(models.Enrichment).filter(models.Enrichment.lot_id == lot_id).one()


def test_single_lot_ai_refuses_a_locked_lot(lots):
    db, made, _ = lots
    for path in ("/lots/lock-ai/enrich", "/lots/lock-ai/inspect", "/lots/lock-ai/comps"):
        r = client.post(path)
        assert r.status_code == 409, (path, r.text)
    assert _e(db, made["ai"]).status == "success"


def test_a_success_the_ai_never_saw_is_not_locked(lots):
    db, made, _ = lots
    r = client.post("/lots/lock-blind/enrich")
    assert r.status_code == 202
    e = _e(db, made["blind"])
    e.status, e.claimed_at = "success", None        # put it back before a worker sees it
    db.commit()


def test_no_reprice_scope_includes_a_locked_lot(lots):
    db, made, auction_id = lots
    ids = ["lock-ai", "lock-comps", "lock-bare"]
    peek = client.post("/lots/reprice", params={"dry_run": "true"},
                       json={"lot_ids": ids}).json()
    assert peek["repricing"] == 2                    # not lock-ai
    unpriced = client.post("/lots/reprice", params={"dry_run": "true", "unpriced_only": "true",
                                                     "auction_ids": auction_id}).json()
    assert unpriced["repricing"] == 2                # lock-bare and lock-blind, never lock-ai


def test_the_worker_step_skips_a_locked_lot_too(lots):
    db, made, _ = lots
    plan = enrich._plan_reprice_lot(db, made["ai"])
    db.rollback()
    assert plan["kind"] == "override"


def test_row_comps_prices_one_lot_with_no_ai(lots, monkeypatch):
    db, made, _ = lots
    looked = []
    monkeypatch.setattr(pricing, "lookup_comps", lambda t: (looked.append(t), dict(FOUND))[1])
    monkeypatch.setattr(pricing, "retail_from_title", lambda *a, **k: None)
    monkeypatch.setattr(enrich, "_verify_gold", lambda *a, **k: None)
    monkeypatch.setattr(enrich, "_call_text", lambda *a, **k: pytest.fail("AI was called"))
    r = client.post("/lots/lock-bare/comps")
    assert r.status_code == 202
    e = _e(db, made["bare"])
    assert e.status == "queued" and e.queued_task == "comps"
    e.claimed_at = datetime.now(timezone.utc)       # held: the dev worker must not take it
    db.commit()
    enrich.run_comps(made["bare"])
    e = _e(db, made["bare"])
    assert looked == ["Lock Test Gadget Model bare"]
    assert e.status == "pending"                     # comps-priced, AI still available
    assert float(e.est_resale) == 30
    assert "no AI" in (e.price_source or "")


def test_ai_price_what_comps_missed_leaves_locked_lots_alone(lots):
    db, made, _ = lots
    e = _e(db, made["ai"])
    e.est_resale = None                              # AI priced it and found nothing
    db.commit()
    before = client.post("/lots/reinspect-no-comps", params={"dry_run": "true"}).json()["lots"]
    e = _e(db, made["ai"])
    e.ai_source = "none"                             # same lot, had the AI never seen it
    db.commit()
    after = client.post("/lots/reinspect-no-comps", params={"dry_run": "true"}).json()["lots"]
    assert after - before == 1
