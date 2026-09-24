"""run_reprice overlaps the gold audits too (`pytest -m db`).

After the comp lookups were made concurrent, the wait left in a bulk
re-price was the gold audit: one model call per fresh gold, run inline
after each lot's comps were written. The audits now go to the pool and
their verdicts are written a batch later, so they overlap each other and
the next batch's lookups.

These pin the contract: the audit calls genuinely overlap, every gold
still gets its verdict, one failing audit costs only its own lot, and the
lot count on the checkpoint is unchanged by any of it.
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

TEST_HIBID = 999999977
N = 8
FOUND = {"est_resale": 90, "price_low": 80, "price_high": 100,
         "comp_count": 8, "price_source": "sold (SoldComps)", "comps": []}


@pytest.fixture
def lots(monkeypatch):
    """N lots that will grade GOLD MINE once re-priced, the job plumbing
    stubbed, the comp lookup instant, the image download skipped."""
    checkpoints = []
    monkeypatch.setattr(enrich.jobs, "start", lambda *a, **k: "test-audit-job")
    monkeypatch.setattr(enrich.jobs, "get", lambda *a, **k: {})
    monkeypatch.setattr(enrich.jobs, "is_cancelled", lambda *a, **k: False)
    monkeypatch.setattr(enrich.jobs, "update",
                        lambda job, **k: checkpoints.append(k.get("current")))
    monkeypatch.setattr(enrich.jobs, "finish", lambda *a, **k: None)
    monkeypatch.setattr(enrich, "_download_image", lambda *a, **k: None)
    monkeypatch.setattr(enrich, "GOLD_CHECK", True)
    monkeypatch.setattr(pricing, "retail_from_title", lambda *a, **k: None)
    monkeypatch.setattr(pricing, "lookup_comps", lambda t: dict(FOUND))
    monkeypatch.setattr(enrich, "REPRICE_CONCURRENCY", 4)

    db = SessionLocal()
    auction = models.Auction(hibid_id=TEST_HIBID, name="reprice-audit-test",
                             buyer_premium_mult=1.15, source="Ship",
                             imported_at=datetime.now(timezone.utc))
    db.add(auction)
    db.flush()
    ids = []
    for n in range(N):
        lot = models.Lot(lot_id=f"ra-{n}", title=f"Audit Test Widget {n}",
                         auction_id=auction.id, current_bid=2, next_bid=3,
                         logistics_ease="EASY", source="Ship")
        db.add(lot)
        db.flush()
        db.add(models.Enrichment(
            lot_id=lot.id, status="success", user_overrides=[],
            enriched_title=f"Audit Test Widget {n}", est_resale=10,
            verdict="normal wear and tear",
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


def _rows(db, ids):
    db.expire_all()
    return {r.lot_id: r for r in
            db.query(models.Enrichment).filter(models.Enrichment.lot_id.in_(ids)).all()}


class _SlowVerdict:
    """A model call that takes a while, counts how many run at once, and
    can be told to blow up on one lot's prompt."""

    def __init__(self, delay=0.4, fail_on=None):
        self.delay, self.fail_on = delay, fail_on
        self.in_flight = self.peak = self.calls = 0
        self.lock = threading.Lock()

    def __call__(self, fn):
        with self.lock:
            self.in_flight += 1
            self.calls += 1
            mine = self.calls
            self.peak = max(self.peak, self.in_flight)
        try:
            time.sleep(self.delay)
            # fn builds the request; it is never sent. The prompt text is
            # reachable through the closure only by running it, so the
            # failure is keyed on the call order instead.
            if self.fail_on is not None and mine == self.fail_on:
                raise RuntimeError("simulated audit failure")
            return {"plausible": True, "reason": "widgets sell here"}
        finally:
            with self.lock:
                self.in_flight -= 1


def test_audits_overlap_and_every_gold_is_confirmed(lots, monkeypatch):
    """The point. Sequential would take N x delay; pooled takes a fraction."""
    db, ids, _ = lots
    verdict = _SlowVerdict(delay=0.4)
    monkeypatch.setattr(enrich, "_call_with_retry", verdict)
    t0 = time.monotonic()
    enrich.run_reprice(ids)
    elapsed = time.monotonic() - t0
    rows = _rows(db, ids)
    assert all(r.roi_status == "GOLD MINE" for r in rows.values()), \
        {k: (r.roi_status, r.roi_reason) for k, r in rows.items()}
    assert verdict.calls == N
    assert verdict.peak >= 2, f"audits never overlapped (peak {verdict.peak})"
    assert elapsed < N * 0.4 * 0.75, f"{elapsed:.2f}s - barely faster than sequential"
    assert all(r.gold_check == "confirmed" for r in rows.values())
    assert all("widgets" in (r.gold_check_note or "") for r in rows.values())


def test_one_failing_audit_costs_only_its_own_lot(lots, monkeypatch):
    db, ids, checkpoints = lots
    monkeypatch.setattr(enrich, "_call_with_retry", _SlowVerdict(delay=0.02, fail_on=3))
    enrich.run_reprice(ids)
    rows = _rows(db, ids)
    checks = sorted((r.gold_check or "none") for r in rows.values())
    assert checks.count("confirmed") == N - 1
    assert checks.count("none") == 1          # left for the next sweep, badge kept
    assert all(r.roi_status == "GOLD MINE" for r in rows.values())
    assert checkpoints[-1] == N and checkpoints == sorted(checkpoints)


def test_a_verdict_for_a_value_that_moved_on_is_dropped(lots):
    """The overlap means a verdict can land after the row changed. It must
    judge the number it was asked about, not whatever is there now."""
    db, ids, _ = lots
    e = _rows(db, ids)[ids[0]]
    plan = {"lot_db_id": ids[0], "candidate": False, "est_resale": 55.0}
    enrich._settle_audit(db, plan, {"plausible": False, "reason": "stale"})
    db.expire_all()
    assert _rows(db, ids)[ids[0]].gold_check is None
    assert float(_rows(db, ids)[ids[0]].est_resale) == pytest.approx(float(e.est_resale))
