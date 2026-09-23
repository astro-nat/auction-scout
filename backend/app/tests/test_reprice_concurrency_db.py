"""run_reprice overlaps its comp lookups (`pytest -m db`).

The wait per lot in a bulk re-price is almost entirely SoldComps' own
latency - its endpoint scrapes eBay live, 5-8s a call - and the loop ran
them one at a time: 1,098 lots took two hours. The lookups now fan out in
batches while every database write stays on the main thread, because a
past version that held transactions open through the network calls starved
the connection pool.

These pin the contract rather than the speed: lookups genuinely overlap,
every lot still gets its result, the resume checkpoint still advances once
per lot in order, one failing lookup takes down only its own lot, and the
empty-lookup guard still holds inside a batch.
"""

import threading
import time
from datetime import datetime, timezone

import pytest

from app import models
from app.database import SessionLocal
from app.services import pricing
from app.workers import enrich

pytestmark = pytest.mark.db

TEST_HIBID = 999999978
N = 8
EMPTY = {"est_resale": None, "price_low": None, "price_high": None,
         "comp_count": 0, "price_source": None, "comps": []}
FOUND = {"est_resale": 60, "price_low": 50, "price_high": 70,
         "comp_count": 6, "price_source": "sold (SoldComps)", "comps": []}


@pytest.fixture
def lots(monkeypatch):
    """N asking-priced lots, the job plumbing stubbed, checkpoints captured."""
    checkpoints = []
    monkeypatch.setattr(enrich.jobs, "start", lambda *a, **k: "test-conc-job")
    monkeypatch.setattr(enrich.jobs, "get", lambda *a, **k: {})
    monkeypatch.setattr(enrich.jobs, "is_cancelled", lambda *a, **k: False)
    monkeypatch.setattr(enrich.jobs, "update",
                        lambda job, **k: checkpoints.append(k.get("current")))
    monkeypatch.setattr(enrich.jobs, "finish", lambda *a, **k: None)
    monkeypatch.setattr(enrich, "_verify_gold", lambda *a, **k: None)
    monkeypatch.setattr(pricing, "retail_from_title", lambda *a, **k: None)
    monkeypatch.setattr(enrich, "REPRICE_CONCURRENCY", 4)

    db = SessionLocal()
    auction = models.Auction(hibid_id=TEST_HIBID, name="reprice-conc-test",
                             buyer_premium_mult=1.15, source="Ship",
                             imported_at=datetime.now(timezone.utc))
    db.add(auction)
    db.flush()
    ids = []
    for n in range(N):
        lot = models.Lot(lot_id=f"rc-{n}", title=f"Conc Test Widget {n}",
                         auction_id=auction.id, current_bid=2, next_bid=3,
                         logistics_ease="EASY", source="Ship")
        db.add(lot)
        db.flush()
        db.add(models.Enrichment(
            lot_id=lot.id, status="success", user_overrides=[],
            enriched_title=f"Conc Test Widget {n}", est_resale=10,
            price_low=8, price_high=12, comp_count=1, price_source="active (eBay)"))
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


def _values(db, ids):
    db.expire_all()
    rows = db.query(models.Enrichment).filter(models.Enrichment.lot_id.in_(ids)).all()
    return {r.lot_id: float(r.est_resale) if r.est_resale is not None else None
            for r in rows}


class _SlowLookup:
    """A lookup that takes a while and counts how many are running at once."""

    def __init__(self, delay=0.4, fail_title=None):
        self.delay, self.fail_title = delay, fail_title
        self.in_flight = self.peak = 0
        self.lock = threading.Lock()

    def __call__(self, title):
        with self.lock:
            self.in_flight += 1
            self.peak = max(self.peak, self.in_flight)
        try:
            time.sleep(self.delay)
            if self.fail_title and self.fail_title in title:
                raise RuntimeError("simulated lookup failure")
            return dict(FOUND)
        finally:
            with self.lock:
                self.in_flight -= 1


def test_lookups_actually_overlap(lots, monkeypatch):
    """The point. Sequential would take N x delay; four-wide takes a quarter."""
    db, ids, _ = lots
    look = _SlowLookup(delay=0.4)
    monkeypatch.setattr(pricing, "lookup_comps", look)
    t0 = time.monotonic()
    enrich.run_reprice(ids)
    elapsed = time.monotonic() - t0
    assert look.peak >= 2, f"lookups never overlapped (peak {look.peak})"
    assert elapsed < N * 0.4 * 0.75, f"{elapsed:.2f}s - barely faster than sequential"
    assert all(v == pytest.approx(60.0) for v in _values(db, ids).values())


def test_the_checkpoint_advances_once_per_lot_in_order(lots, monkeypatch):
    """Resume depends on this. A deploy mid-run restarts from `current`,
    so it must be exactly the count of lots fully handled, in list order."""
    db, ids, checkpoints = lots
    monkeypatch.setattr(pricing, "lookup_comps", _SlowLookup(delay=0.05))
    enrich.run_reprice(ids)
    assert checkpoints == list(range(1, N + 1))


def test_one_failing_lookup_takes_down_only_its_own_lot(lots, monkeypatch):
    """A raised lookup in a batch of four must not cost the other three, and
    the failed lot keeps the value it had rather than losing it."""
    db, ids, checkpoints = lots
    monkeypatch.setattr(pricing, "lookup_comps",
                        _SlowLookup(delay=0.05, fail_title="Widget 2"))
    enrich.run_reprice(ids)
    vals = _values(db, ids)
    failed = ids[2]
    assert vals[failed] == pytest.approx(10.0), "failed lot lost its value"
    assert all(vals[i] == pytest.approx(60.0) for i in ids if i != failed)
    assert checkpoints[-1] == N, "the run did not reach the end"


def test_the_empty_guard_holds_inside_a_batch(lots, monkeypatch):
    """Concurrency must not reopen the data-loss bug fixed this morning."""
    db, ids, _ = lots
    monkeypatch.setattr(pricing, "lookup_comps", lambda *a, **k: dict(EMPTY))
    enrich.run_reprice(ids)
    assert all(v == pytest.approx(10.0) for v in _values(db, ids).values())
    empties = (db.query(models.PriceObservation)
                 .filter(models.PriceObservation.lot_id.in_(ids),
                         models.PriceObservation.method == "empty").count())
    assert empties == N


def test_width_one_is_the_old_sequential_behaviour(lots, monkeypatch):
    """REPRICE_CONCURRENCY=1 must still be correct - it is the escape hatch."""
    db, ids, checkpoints = lots
    monkeypatch.setattr(enrich, "REPRICE_CONCURRENCY", 1)
    look = _SlowLookup(delay=0.02)
    monkeypatch.setattr(pricing, "lookup_comps", look)
    enrich.run_reprice(ids)
    assert look.peak == 1
    assert all(v == pytest.approx(60.0) for v in _values(db, ids).values())
    assert checkpoints == list(range(1, N + 1))


def test_a_hand_corrected_lot_is_skipped_without_a_lookup(lots, monkeypatch):
    """Existing contract, kept across the rewrite."""
    db, ids, _ = lots
    e = db.query(models.Enrichment).filter(models.Enrichment.lot_id == ids[0]).one()
    e.user_overrides = ["est_resale"]
    e.est_resale = 99
    db.commit()
    seen = []
    monkeypatch.setattr(pricing, "lookup_comps",
                        lambda t: (seen.append(t), dict(FOUND))[1])
    enrich.run_reprice(ids)
    assert not any("Widget 0" in t for t in seen), "looked up a hand-corrected lot"
    assert _values(db, ids)[ids[0]] == pytest.approx(99.0)
