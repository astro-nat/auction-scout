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
    def run(lot, e, verdict, *, candidate=False):
        calls = []

        def fake_call(fn):
            calls.append(1)
            return verdict

        monkeypatch.setattr(enrich, "_download_image", lambda *a: None)
        monkeypatch.setattr(enrich, "_call_with_retry", fake_call)
        enrich._verify_gold(_FakeDB(), lot, e, candidate=candidate)
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


def test_recomputing_roi_cannot_resurrect_a_demoted_gold(verify_with):
    """Bid refresh and reprice re-run _apply_roi; a standing demotion must
    survive that arithmetic. 81 zombie golds came back this way before."""
    lot, e = _gold_lot()
    verify_with(lot, e, {"plausible": False, "reason": "wrong comps"})
    assert e.roi_status == "PASS"
    lot.current_bid = 12          # a new bid arrives, ROI recomputes
    enrich._apply_roi(lot, e)
    assert e.roi_status == "PASS"
    assert e.gold_check == "demoted"


def test_a_garbage_verdict_changes_nothing(verify_with):
    lot, e = _gold_lot()
    verify_with(lot, e, {"plausible": "yes", "reason": 3})
    assert e.gold_check is None
    assert e.roi_status == "GOLD MINE"


# --- the auditor's own number replaces the one it rejected ---------------

def test_a_rejected_value_is_replaced_by_the_auditors_own(verify_with):
    """The SCS egg case: comps said $370, the audit note said $80-150 — the
    old code kept $370 on display and threw the note's number away."""
    lot, e = _gold_lot()
    verify_with(lot, e, {"plausible": False,
                         "reason": "common year sells for far less",
                         "realistic_value": 80})
    assert e.gold_check == "corrected"
    assert float(e.est_resale) == 80
    assert e.price_source == "audit-corrected (comps said $200)"
    # Regraded on the corrected number — at an $80 value and a $12 bid this
    # is a REAL gold, not a lot stuck in demotion limbo.
    assert e.roi_status == "GOLD MINE"
    assert e.roi_reason is None
    assert "common year" in e.gold_check_note


def test_a_correction_can_still_fail_the_roi_bar(verify_with):
    lot, e = _gold_lot()
    lot.current_bid, lot.next_bid = 60, 65
    verify_with(lot, e, {"plausible": False, "reason": "x",
                         "realistic_value": 80})
    assert e.gold_check == "corrected"
    assert e.roi_status == "PASS"          # $65 bid on an $80 item


def test_zero_realistic_value_is_a_plain_demotion(verify_with):
    lot, e = _gold_lot()
    verify_with(lot, e, {"plausible": False, "reason": "decorative only",
                         "realistic_value": 0})
    assert e.gold_check == "demoted"
    assert float(e.est_resale) == 200      # nothing to replace it with
    assert e.roi_status == "PASS"


def test_a_higher_value_confirms_the_claim_as_a_floor(verify_with):
    """'Implausible, and it's worth MORE' used to be read as incoherent and
    demoted. It is not incoherent - it is an auditor agreeing the number on
    display is at least right. A $1-bid LeBron rookie at $56 was demoted
    to PASS by an audit saying it 'typically sells for $200-$400'. The
    claim stands as a floor and keeps the badge; it is NOT raised to the
    auditor's figure, because one opinion pushing a value up is the risk
    the audit exists to catch in the other direction."""
    lot, e = _gold_lot()
    verify_with(lot, e, {"plausible": False, "reason": "x",
                         "realistic_value": 500})
    assert e.gold_check == "confirmed"
    assert float(e.est_resale) == 200
    assert e.roi_status == "GOLD MINE"


def test_a_corrected_retail_gold_survives_the_thin_evidence_gate(verify_with):
    """Retail-in-title golds have few or no comps; once the price_source no
    longer starts with 'retail $' the thin gate would kill the correction."""
    lot, e = _gold_lot(comp_count=0, price_source="retail $199 in title")
    verify_with(lot, e, {"plausible": False, "reason": "sells under retail",
                         "realistic_value": 80})
    assert e.gold_check == "corrected"
    assert e.roi_status == "GOLD MINE"


# --- thin-evidence candidates: the audit as promoter ---------------------

def _thin_lot(**over):
    """A comp-less lot the itemized vision pass valued — blocked by the
    thin gate, eligible for a promotion audit."""
    defaults = dict(comp_count=0, price_source="itemized (vision)",
                    roi_status="PASS",
                    roi_reason="only 0 comps — the badge needs 2 agreeing")
    defaults.update(over)
    return _gold_lot(**defaults)


def test_a_confirmed_candidate_earns_the_badge(verify_with):
    lot, e = _thin_lot()
    calls = verify_with(lot, e, {"plausible": True,
                                 "reason": "the spines add up"},
                        candidate=True)
    assert calls == 1
    assert e.gold_check == "confirmed"
    assert e.roi_status == "GOLD MINE"
    assert e.roi_reason is None


def test_a_promotion_survives_a_bid_refresh(verify_with):
    lot, e = _thin_lot()
    verify_with(lot, e, {"plausible": True, "reason": "x"}, candidate=True)
    lot.current_bid, lot.next_bid = 12, 14
    enrich._apply_roi(lot, e)
    assert e.roi_status == "GOLD MINE"      # confirmed exempts the thin gate


def test_a_corrected_candidate_regrades_on_the_honest_number(verify_with):
    """$225 from one comp-less inspection, auditor says $80 — at a $10 bid
    that's STILL a real gold, on a number someone actually defended."""
    lot, e = _thin_lot(est_resale=225)
    verify_with(lot, e, {"plausible": False, "reason": "sums a bit hot",
                         "realistic_value": 80}, candidate=True)
    assert e.gold_check == "corrected"
    assert float(e.est_resale) == 80
    assert e.roi_status == "GOLD MINE"


def test_a_demoted_candidate_stays_blocked(verify_with):
    lot, e = _thin_lot()
    verify_with(lot, e, {"plausible": False, "reason": "junk pile",
                         "realistic_value": 0}, candidate=True)
    assert e.gold_check == "demoted"
    assert e.roi_status == "PASS"


def test_non_candidates_still_require_the_badge(verify_with):
    lot, e = _thin_lot()
    calls = verify_with(lot, e, {"plausible": True, "reason": "x"})
    assert calls == 0                        # PASS + not a candidate = no spend
    assert e.roi_status == "PASS"


def test_a_correction_survives_a_bid_refresh(verify_with):
    lot, e = _gold_lot()
    verify_with(lot, e, {"plausible": False, "reason": "x",
                         "realistic_value": 80})
    assert e.roi_status == "GOLD MINE"
    lot.current_bid, lot.next_bid = 13, 14     # new bid (still under the
                                               # $15.91 ceiling), ROI recomputes
    enrich._apply_roi(lot, e)
    assert e.roi_status == "GOLD MINE"
    assert e.gold_check == "corrected"         # and no re-audit gate flip
