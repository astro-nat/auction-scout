"""The run_reprice paths the concurrency rewrite touched (`pytest -m db`).

test_reprice_concurrency pins the overlap contract with every lot on the
plain network path. These pin everything else the loop does, because the
rewrite split one body into three phases and each seam is a place for an
old behaviour to fall through:

  - resume from a checkpoint - the property a mid-run deploy relied on
    today: the reaper restarted the job from `current`, and lots before
    it must not be touched again
  - cancellation, now checked per batch
  - a lot deleted mid-run, and a plan-phase failure, both of which must
    still advance the checkpoint or resume replays them forever
  - the cheap-retail path (no network) and the expensive-retail path
    (a market cross-check), which the concurrency tests stub away
  - the gold audit reset: a changed value voids its old audit, an
    unchanged one keeps it
"""

from datetime import datetime, timezone

import pytest

from app import models
from app.database import SessionLocal
from app.services import pricing
from app.workers import enrich

pytestmark = pytest.mark.db

TEST_HIBID = 999999979
N = 6
EMPTY = {"est_resale": None, "price_low": None, "price_high": None,
         "comp_count": 0, "price_source": None, "comps": []}
FOUND = {"est_resale": 60, "price_low": 50, "price_high": 70,
         "comp_count": 6, "price_source": "sold (SoldComps)", "comps": []}
TITLED = {"est_resale": 45, "price_low": 45, "price_high": 45,
          "comp_count": 0, "price_source": "retail $90 in title", "comps": []}


@pytest.fixture
def lots(monkeypatch):
    """N lots at $10 from asking listings; job plumbing stubbed and the
    checkpoint sequence captured."""
    checkpoints = []
    monkeypatch.setattr(enrich.jobs, "start", lambda *a, **k: "test-paths-job")
    monkeypatch.setattr(enrich.jobs, "get", lambda *a, **k: {})
    monkeypatch.setattr(enrich.jobs, "is_cancelled", lambda *a, **k: False)
    monkeypatch.setattr(enrich.jobs, "update",
                        lambda job, **k: checkpoints.append(k.get("current")))
    monkeypatch.setattr(enrich.jobs, "finish", lambda *a, **k: None)
    monkeypatch.setattr(enrich, "_verify_gold", lambda *a, **k: None)
    monkeypatch.setattr(pricing, "retail_from_title", lambda *a, **k: None)
    monkeypatch.setattr(pricing, "lookup_comps", lambda *a, **k: dict(FOUND))
    monkeypatch.setattr(enrich, "REPRICE_CONCURRENCY", 2)   # two batches of 2, plus 2

    db = SessionLocal()
    auction = models.Auction(hibid_id=TEST_HIBID, name="reprice-paths-test",
                             buyer_premium_mult=1.15, source="Ship",
                             imported_at=datetime.now(timezone.utc))
    db.add(auction)
    db.flush()
    ids = []
    for n in range(N):
        lot = models.Lot(lot_id=f"rp-{n}", title=f"Paths Test Widget {n}",
                         auction_id=auction.id, current_bid=2, next_bid=3,
                         logistics_ease="EASY", source="Ship")
        db.add(lot)
        db.flush()
        db.add(models.Enrichment(
            lot_id=lot.id, status="success", user_overrides=[],
            enriched_title=f"Paths Test Widget {n}", est_resale=10,
            price_low=8, price_high=12, comp_count=1, price_source="active (eBay)",
            gold_check="confirmed", gold_check_note="was fine"))
        ids.append(lot.id)
    db.commit()
    yield db, ids, checkpoints
    db.rollback()
    db.query(models.PriceObservation).filter(
        models.PriceObservation.lot_id.in_(ids)).delete(synchronize_session=False)
    db.query(models.Enrichment).filter(
        models.Enrichment.lot_id.in_(ids)).delete(synchronize_session=False)
    db.query(models.Lot).filter(models.Lot.id.in_(ids)).delete(synchronize_session=False)
    db.query(models.Auction).filter(models.Auction.id == auction.id).delete(
        synchronize_session=False)
    db.commit()
    db.close()


def _rows(db, ids):
    db.expire_all()
    return {r.lot_id: r for r in db.query(models.Enrichment)
            .filter(models.Enrichment.lot_id.in_(ids)).all()}


def test_resume_starts_at_the_checkpoint_and_leaves_earlier_lots_alone(lots, monkeypatch):
    """The deploy-mid-run contract. The reaper hands back resume_job_id and
    the job row says current=3: lots 1-3 are done and must not be priced
    again, lots 4-6 must be, and the checkpoint continues from 4."""
    db, ids, checkpoints = lots
    monkeypatch.setattr(enrich.jobs, "get", lambda *a, **k: {"current": 3})
    seen = []
    monkeypatch.setattr(pricing, "lookup_comps",
                        lambda t: (seen.append(t), dict(FOUND))[1])
    enrich.run_reprice(ids, resume_job_id="test-paths-job")
    rows = _rows(db, ids)
    assert [float(rows[i].est_resale) for i in ids[:3]] == [10, 10, 10]
    assert [float(rows[i].est_resale) for i in ids[3:]] == [60, 60, 60]
    assert not any(f"Widget {n}" in t for t in seen for n in (0, 1, 2))
    assert checkpoints == [4, 5, 6]


def test_cancellation_stops_at_a_batch_boundary(lots, monkeypatch):
    """Cancel is read once per batch. With width 2, cancelling after the
    first check means exactly one batch ran and the rest were left."""
    db, ids, checkpoints = lots
    calls = {"n": 0}

    def cancelled(job):
        calls["n"] += 1
        return calls["n"] > 1
    monkeypatch.setattr(enrich.jobs, "is_cancelled", cancelled)
    enrich.run_reprice(ids)
    rows = _rows(db, ids)
    assert [float(rows[i].est_resale) for i in ids] == [60, 60, 10, 10, 10, 10]
    assert checkpoints == [1, 2]


def test_a_lot_deleted_mid_run_still_advances_the_checkpoint(lots):
    """`current` must move on every lot or resume replays this one forever.
    A missing lot is the case that used to `continue` past the update."""
    db, ids, checkpoints = lots
    with_ghost = ids[:2] + [10**9] + ids[2:]          # an id that exists nowhere
    enrich.run_reprice(with_ghost)
    rows = _rows(db, ids)
    assert all(float(rows[i].est_resale) == 60 for i in ids)
    assert checkpoints == list(range(1, N + 2))


def test_a_plan_phase_failure_costs_only_its_own_lot(lots, monkeypatch):
    """Phase A raises for one lot (the logistics classifier here). The
    batch mate and every later lot are still priced, the session is rolled
    back rather than poisoned, and the checkpoint reaches the end."""
    db, ids, checkpoints = lots
    real = enrich.classify_logistics

    def flaky(title, *a, **k):
        if "Widget 1" in title:
            raise RuntimeError("simulated classifier failure")
        return real(title, *a, **k)
    monkeypatch.setattr(enrich, "classify_logistics", flaky)
    enrich.run_reprice(ids)
    rows = _rows(db, ids)
    assert float(rows[ids[1]].est_resale) == 10, "failed lot should be untouched"
    assert all(float(rows[i].est_resale) == 60 for i in ids if i != ids[1])
    assert checkpoints[-1] == N


def test_a_cheap_retail_claim_is_priced_from_the_title_with_no_lookup(lots, monkeypatch):
    """Under RETAIL_VERIFY_MIN the printed price stands and the network is
    never touched - the concurrency tests stub this path away entirely."""
    db, ids, _ = lots
    cheap = pricing.RETAIL_VERIFY_MIN - 1
    monkeypatch.setattr(pricing, "retail_from_title",
                        lambda t: cheap if "Widget 0" in t else None)
    monkeypatch.setattr(pricing, "price_from_title", lambda t: dict(TITLED))
    monkeypatch.setattr(pricing, "condition_from_title", lambda t: "normal wear and tear")

    def boom(t):
        assert "Widget 0" not in t, "looked up comps for a cheap retail claim"
        return dict(FOUND)
    monkeypatch.setattr(pricing, "lookup_comps", boom)
    enrich.run_reprice(ids)
    rows = _rows(db, ids)
    assert float(rows[ids[0]].est_resale) == 45
    assert rows[ids[0]].price_source.startswith("retail $")
    assert rows[ids[0]].verdict == "normal wear and tear"
    assert all(float(rows[i].est_resale) == 60 for i in ids[1:])


def test_an_expensive_retail_claim_is_cross_checked_off_thread(lots, monkeypatch):
    """At or above RETAIL_VERIFY_MIN the claim goes through
    verified_title_price - a network call, so it must ride the thread
    phase like any lookup - and the verdict is re-read from the title."""
    db, ids, _ = lots
    pricey = pricing.RETAIL_VERIFY_MIN + 100
    monkeypatch.setattr(pricing, "retail_from_title",
                        lambda t: pricey if "Widget 0" in t else None)
    monkeypatch.setattr(pricing, "condition_from_title", lambda t: "mint condition or working perfectly")
    verified = []
    monkeypatch.setattr(pricing, "verified_title_price",
                        lambda title, search: (verified.append(title), dict(FOUND))[1])
    enrich.run_reprice(ids)
    rows = _rows(db, ids)
    assert verified and "Widget 0" in verified[0]
    assert float(rows[ids[0]].est_resale) == 60
    assert rows[ids[0]].verdict == "mint condition or working perfectly"


def test_a_changed_value_voids_its_old_audit_and_an_unchanged_one_keeps_it(lots, monkeypatch):
    """The audit certified a number. If the number changes the certificate
    is void; if the guard kept the old number, the certificate still holds."""
    db, ids, _ = lots
    monkeypatch.setattr(pricing, "lookup_comps",
                        lambda t: dict(EMPTY) if "Widget 0" in t else dict(FOUND))
    enrich.run_reprice(ids)
    rows = _rows(db, ids)
    kept, changed = rows[ids[0]], rows[ids[1]]
    assert float(kept.est_resale) == 10 and kept.gold_check == "confirmed"
    assert float(changed.est_resale) == 60 and changed.gold_check is None
    assert changed.gold_check_note is None
