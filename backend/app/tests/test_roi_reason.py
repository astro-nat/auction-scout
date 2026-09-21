"""Every PASS names the gate that blocked it, in the order the gates fire —
the sports-card auction question ("why are these not gold mines?") answered
per lot instead of per conversation.
"""

import pytest

from app import models
from app.workers import enrich


def _lot(**over):
    defaults = dict(lot_id="1", title="Widget", logistics_ease="EASY",
                    source="Ship", current_bid=5, next_bid=6,
                    unreachable_pickup=False,
                    auction=models.Auction(name="a", source="Ship",
                                           buyer_premium_mult=1.18))
    defaults.update(over)
    lot = models.Lot(**defaults)
    e = models.Enrichment(lot_id=1, user_overrides=[])
    lot.enrichment = e
    return lot, e


def _grade(lot, e, *, resale=100, comps=5, verdict="mint condition or working perfectly",
           source="sold (SoldComps)", check=None):
    e.est_resale = resale
    e.comp_count = comps
    e.verdict = verdict
    e.price_source = source
    e.gold_check = check
    enrich._apply_roi(lot, e)
    return e


def test_clean_gold_needs_no_explanation():
    lot, e = _lot()
    _grade(lot, e)
    assert e.roi_status == "GOLD MINE"
    assert e.roi_reason is None


def test_red_flag_names_the_verdict():
    lot, e = _lot()
    _grade(lot, e, verdict="untested or unknown condition")
    assert e.roi_status == "PASS"
    assert e.roi_reason == "condition red flag: untested or unknown condition"


def test_unreachable_pickup_says_so():
    lot, e = _lot(unreachable_pickup=True)
    _grade(lot, e)
    assert e.roi_reason == "pickup-only and outside your radius"


def test_thin_evidence_counts_its_comps():
    lot, e = _lot()
    _grade(lot, e, comps=1)
    assert e.roi_reason == "only 1 comp — the badge needs 2 agreeing"


def test_demotion_points_at_the_audit():
    lot, e = _lot()
    _grade(lot, e, check="demoted")
    assert e.roi_reason == "audit demoted the value (see its note)"


def test_bid_past_ceiling_shows_both_numbers():
    lot, e = _lot(current_bid=90, next_bid=95)
    _grade(lot, e, resale=60)
    assert e.roi_status == "PASS"
    # $95, not $90: the grader prices the NEXT bid — what you'd actually pay.
    assert "past the" in e.roi_reason and "$95" in e.roi_reason


def test_worthless_lot_is_named_worthless():
    lot, e = _lot()
    _grade(lot, e, resale=3)      # ceiling rounds to zero after costs
    assert e.roi_reason == "value too low to clear costs at any bid"


def test_no_value_at_all():
    lot, e = _lot()
    e.comp_count = 0
    e.verdict = "mint condition or working perfectly"
    enrich._apply_roi(lot, e)
    assert e.roi_reason == "no resale value found"


def test_gates_fire_in_order():
    """A lot failing several gates reports the FIRST — red flag beats thin."""
    lot, e = _lot()
    _grade(lot, e, comps=1, verdict="broken, damaged, or for parts")
    assert e.roi_reason.startswith("condition red flag")


def test_reason_refreshes_when_the_gate_changes():
    lot, e = _lot(current_bid=90, next_bid=95)
    _grade(lot, e, resale=60)
    assert "past the" in e.roi_reason
    lot.current_bid, lot.next_bid = 5, 6          # bidding reset (relist)
    enrich._apply_roi(lot, e)
    assert e.roi_status == "GOLD MINE"
    assert e.roi_reason is None                   # stale reason cleared