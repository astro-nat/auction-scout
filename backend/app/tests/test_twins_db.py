"""Same title, one pricing, through the pipeline (`pytest -m db`).

Test rows are never left unclaimed in 'queued', so the dev worker running
beside the suite cannot take one and spend a real API call on it.
"""

from datetime import datetime, timezone

import pytest

from app import models
from app.database import SessionLocal
from app.services import pricing
from app.workers import enrich

pytestmark = pytest.mark.db

TEST_HIBID = 999999974
ALARM = "WZU Recordable Personal Safety Alarm - FCC Certified"
FOUND = {"est_resale": 14, "price_low": 12, "price_high": 16,
         "comp_count": 9, "price_source": "sold (SoldComps)", "comps": []}


@pytest.fixture
def env(monkeypatch):
    calls = {"ai": 0, "comps": []}

    def fake_ai(*a, **k):
        calls["ai"] += 1
        return {"enriched_title": "WZU Personal Safety Alarm 130dB", "verdict": "normal wear and tear",
                "confident": True, "notes": "n", "ship": "EASY"}

    monkeypatch.setattr(enrich, "_call_text", fake_ai)
    monkeypatch.setattr(enrich, "_download_image", lambda *a, **k: None)
    monkeypatch.setattr(enrich, "_verify_gold", lambda *a, **k: None)
    monkeypatch.setattr(pricing, "verified_title_price", lambda *a, **k: None)
    monkeypatch.setattr(pricing, "retail_from_title", lambda *a, **k: None)
    monkeypatch.setattr(pricing, "lookup_comps",
                        lambda t: (calls["comps"].append(t), dict(FOUND))[1])
    for name, fn in (("start", lambda *a, **k: "test-twin-job"), ("get", lambda *a, **k: {}),
                     ("is_cancelled", lambda *a, **k: False),
                     ("update", lambda *a, **k: None), ("finish", lambda *a, **k: None)):
        monkeypatch.setattr(enrich.jobs, name, fn)

    db = SessionLocal()
    auction = models.Auction(hibid_id=TEST_HIBID, name="twins-test sale",
                             buyer_premium_mult=1.15, source="Ship",
                             imported_at=datetime.now(timezone.utc))
    db.add(auction)
    db.flush()
    made = []

    def lot(title, status="pending", bid=1, **enr):
        row = models.Lot(lot_id=f"tw-{len(made)}", title=title, description="x" * 200,
                         auction_id=auction.id, current_bid=bid, next_bid=bid + 1,
                         logistics_ease="EASY", source="Ship")
        db.add(row)
        db.flush()
        claimed = datetime.now(timezone.utc) if status == "queued" else None
        enr.setdefault("user_overrides", [])
        db.add(models.Enrichment(lot_id=row.id, status=status, queued_task="enrich",
                                 claimed_at=claimed, **enr))
        db.commit()
        made.append(row.id)
        return row.id

    yield db, lot, calls
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


def test_one_pricing_flows_to_every_same_title_lot(env):
    db, lot, calls = env
    first = lot(ALARM, status="queued")
    others = [lot(ALARM), lot(ALARM.lower().replace(" - ", ", "), bid=9)]
    enrich.run_enrichment(first)
    assert calls["ai"] == 1 and len(calls["comps"]) == 1
    src = _e(db, first)
    for o in others:
        e = _e(db, o)
        assert e.status == "success"
        assert float(e.est_resale) == float(src.est_resale)
        assert e.enriched_title == src.enriched_title
        assert "same title as lot" in e.price_source
    # Bid guidance is each lot's own: a $9 bid is not a $1 bid.
    assert _e(db, others[1]).max_bid == src.max_bid
    assert _e(db, others[1]).est_roi != src.est_roi


def test_a_new_lot_copies_a_priced_twin_and_spends_nothing(env):
    db, lot, calls = env
    lot(ALARM, status="success", est_resale=14, enriched_title="WZU Alarm",
        price_source="sold (SoldComps)", comp_count=9)
    newcomer = lot(ALARM, status="queued")
    enrich.run_enrichment(newcomer)
    assert calls["ai"] == 0 and calls["comps"] == []
    e = _e(db, newcomer)
    assert e.status == "success" and float(e.est_resale) == 14


def test_a_twin_the_worker_is_holding_is_left_alone(env):
    db, lot, calls = env
    first = lot(ALARM, status="queued")
    held = lot(ALARM, status="queued")          # claimed just now: in flight
    enrich.run_enrichment(first)
    assert _e(db, held).status == "queued"


def test_generic_titles_are_priced_one_by_one(env):
    db, lot, calls = env
    a = lot("Pyrex dish", status="queued")
    b = lot("Pyrex dish")
    enrich.run_enrichment(a)
    assert _e(db, b).status == "pending" and _e(db, b).est_resale is None


def test_a_hand_set_price_is_never_overwritten(env):
    db, lot, calls = env
    first = lot(ALARM, status="queued")
    mine = lot(ALARM, status="success", est_resale=40, user_overrides=["est_resale"])
    enrich.run_enrichment(first)
    assert float(_e(db, mine).est_resale) == 40


def test_a_reprice_looks_each_title_up_once(env):
    db, lot, calls = env
    ids = [lot(ALARM, status="success", est_resale=5, enriched_title="WZU Alarm")
           for _ in range(3)]
    ids.append(lot("Hiccapop Convertible Crib Bed Rail White", status="success",
                   est_resale=5, enriched_title="Hiccapop Crib Rail"))
    enrich.run_reprice(ids)
    assert len(calls["comps"]) == 2
    values = {float(_e(db, i).est_resale) for i in ids[:3]}
    assert len(values) == 1
