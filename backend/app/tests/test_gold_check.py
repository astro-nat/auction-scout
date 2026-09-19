"""The gold audit — every fresh GOLD MINE gets a second AI opinion.

A GOLD MINE is the app telling you to spend money; the audits kept finding
golds built on the wrong comps (costume priced as fine jewelry, one item
carrying a pile's total). No network here — the model call and image
download are monkeypatched.
"""

import pytest

from app import models
from app.workers import enrich


class _FakeDB:
    def commit(self):
        pass


def _gold_lot(**enrichment_kw):
    lot = models.Lot(lot_id="1", title="Navajo Sterling Cuff",
                     description="stamped sterling", current_bid=10, next_bid=12,
                     thumbnail_url=None, logistics_ease="EASY",
                     auction=models.Auction(name="a", buyer_premium_mult=1.15))
    defaults = dict(lot_id=1, status="success", est_resale=200, comp_count=5,
                    price_source="5 sold comps", max_bid=80, est_roi=1.5,
                    roi_status="GOLD MINE", user_overrides=[])
    defaults.update(enrichment_kw)
    e = models.Enrichment(**defaults)
    lot.enrichment = e
    return lot, e


@pytest.fixture
def verify_with(monkeypatch):
    """Run _verify_gold with a canned model verdict, counting model calls."""
    def run(lot, e, verdict):
        calls = []

        def fake_call(fn):
            calls.append(1)
            return verdict

        monkeypatch.setattr(enrich, "_download_image", lambda *a: None)
        monkeypatch.setattr(enrich, "_call_with_retry", fake_call)
        enrich._verify_gold(_FakeDB(), lot, e)
        return len(calls)
    return run


def test_a_plausible_gold_is_confirmed(verify_with):
    lot, e = _gold_lot()
    verify_with(lot, e, {"plausible": True, "reason": "sterling cuffs sell here"})
    assert e.gold_check == "confirmed"
    assert e.roi_status == "GOLD MINE"
    assert "sterling" in e.gold_check_note


def test_an_implausible_gold_is_demoted_to_pass(verify_with):
    lot, e = _gold_lot()
    verify_with(lot, e, {"plausible": False, "reason": "comps matched a premium maker"})
    assert e.gold_check == "demoted"
    assert e.roi_status == "PASS"
    assert "premium" in e.gold_check_note


def test_non_gold_lots_are_never_audited(verify_with):
    lot, e = _gold_lot(roi_status="PASS")
    calls = verify_with(lot, e, {"plausible": False, "reason": "x"})
    assert calls == 0
    assert e.gold_check is None


def test_an_already_checked_gold_is_not_rechecked(verify_with):
    lot, e = _gold_lot(gold_check="confirmed")
    calls = verify_with(lot, e, {"plausible": False, "reason": "x"})
    assert calls == 0
    assert e.gold_check == "confirmed"


def test_hand_set_prices_are_not_second_guessed(verify_with):
    lot, e = _gold_lot(user_overrides=["est_resale"])
    calls = verify_with(lot, e, {"plausible": False, "reason": "x"})
    assert calls == 0
    assert e.roi_status == "GOLD MINE"


def test_a_failed_call_leaves_the_gold_standing_but_unchecked(verify_with):
    """Fail-open: the badge stays, gold_check stays NULL, and the next
    reprice retries the audit."""
    lot, e = _gold_lot()
    verify_with(lot, e, None)
    assert e.gold_check is None
    assert e.roi_status == "GOLD MINE"


def test_the_kill_switch_disables_the_audit(verify_with, monkeypatch):
    monkeypatch.setattr(enrich, "GOLD_CHECK", False)
    lot, e = _gold_lot()
    calls = verify_with(lot, e, {"plausible": False, "reason": "x"})
    assert calls == 0
    assert e.roi_status == "GOLD MINE"


def test_a_garbage_verdict_changes_nothing(verify_with):
    lot, e = _gold_lot()
    verify_with(lot, e, {"plausible": "yes", "reason": 3})
    assert e.gold_check is None
    assert e.roi_status == "GOLD MINE"
