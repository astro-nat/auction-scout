"""The user's gold-mine switches. Pure - the setting is stubbed."""

import pytest

from app import models
from app.services import gold_rules, mailer
from app.workers import enrich


def _rules(monkeypatch, **changes):
    monkeypatch.setattr(gold_rules, "current", lambda: {**gold_rules.DEFAULTS, **changes})


def _grade(title="Pampered Chef 1-Quart Enameled Cast Iron Baker", *, resale=60,
           comps=5, bid=2):
    lot = models.Lot(lot_id="1", title=title, description="", logistics_ease="NEUTRAL",
                     source="Local Pickup", current_bid=bid, next_bid=bid,
                     unreachable_pickup=False,
                     auction=models.Auction(name="a", source="Local Pickup",
                                            buyer_premium_mult=1.18))
    e = models.Enrichment(lot_id=1, user_overrides=[], est_resale=resale, comp_count=comps,
                          verdict="normal wear and tear", price_source="sold (SoldComps)")
    lot.enrichment = e
    enrich._apply_roi(lot, e)
    return e


@pytest.fixture(autouse=True)
def _easy_target(monkeypatch):
    from app.services import financials
    monkeypatch.setattr(financials, "current_target_roi", lambda: 0.5)


def test_mailer_switch(monkeypatch):
    _rules(monkeypatch)
    assert _grade().roi_reason == "won't fit your 14.5 x 20 in mailer"
    _rules(monkeypatch, mailer=False)
    assert _grade().roi_status == "GOLD MINE"


def test_weight_switch_and_limit(monkeypatch):
    title = "Drive Medical Cruiser III Folding Wheelchair"     # ~40 lb
    _rules(monkeypatch, mailer=False)
    assert _grade(title).roi_reason == "about 40 lb - over your 10 lb limit"
    _rules(monkeypatch, mailer=False, max_weight_lb=50)
    assert _grade(title).roi_status == "GOLD MINE"
    _rules(monkeypatch, mailer=False, weight=False)
    assert _grade(title).roi_status == "GOLD MINE"


def test_min_profit_switch_and_amount(monkeypatch):
    title = "Signed Kenneth Lane Necklace"
    _rules(monkeypatch, min_profit_usd=40)
    e = _grade(title, resale=40, bid=6)
    assert e.roi_status == "PASS" and "$40" in e.roi_reason
    _rules(monkeypatch, min_profit=False)
    assert _grade(title, resale=40, bid=6).roi_status == "GOLD MINE"


def test_two_comps_switch(monkeypatch):
    title = "Signed Kenneth Lane Necklace"
    _rules(monkeypatch)
    assert _grade(title, comps=1).roi_reason == "only 1 comp — the badge needs 2 agreeing"
    _rules(monkeypatch, two_comps=False)
    assert _grade(title, comps=1).roi_status == "GOLD MINE"


def test_pricing_skip_follows_the_switches(monkeypatch):
    _rules(monkeypatch)
    assert mailer.wont_be_gold("Ashley Leather Sofa", "", "HARD", (14.5, 20)) is not None
    _rules(monkeypatch, mailer=False, weight=False)
    assert mailer.wont_be_gold("Ashley Leather Sofa", "", "HARD", (14.5, 20)) is None


@pytest.mark.parametrize("patch,error", [
    ({"mailer": "yes"}, "mailer must be true or false"),
    ({"max_weight_lb": 0}, "max_weight_lb must be a number from 0.1 to 1000"),
    ({"min_profit_usd": True}, "min_profit_usd must be a number from 0 to 10000"),
    ({"str": True}, "unknown gold-mine rule: str"),
])
def test_merge_rejects_bad_values(monkeypatch, patch, error):
    monkeypatch.setattr(gold_rules, "current", lambda: dict(gold_rules.DEFAULTS))
    with pytest.raises(ValueError, match=error):
        gold_rules.merge(patch)


def test_merge_applies_a_partial_change(monkeypatch):
    monkeypatch.setattr(gold_rules, "current", lambda: dict(gold_rules.DEFAULTS))
    rules = gold_rules.merge({"two_comps": False, "min_profit_usd": 15})
    assert rules["two_comps"] is False and rules["min_profit_usd"] == 15.0
    assert rules["mailer"] is True
