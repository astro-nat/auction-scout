"""ROI math — pure functions, explicit targets so the settings store isn't hit."""

import pytest

from app.services import financials


def test_max_bid_zero_when_resale_cannot_cover_costs():
    assert financials.max_bid(resale_value=5.0, logistics_penalty=60.0,
                              target_roi=1.5) == 0.0


def test_max_bid_scales_down_with_higher_target():
    low_bar = financials.max_bid(100.0, 15.0, target_roi=1.0)
    high_bar = financials.max_bid(100.0, 15.0, target_roi=5.0)
    assert high_bar < low_bar


def test_evaluate_lead_gold_vs_pass():
    good = financials.evaluate_lead(resale_value=200.0, current_bid=3.0,
                                    logistics_penalty=15.0, dts=10, target_roi=1.5)
    assert good.status == "GOLD MINE"
    assert good.profit > 0

    bad = financials.evaluate_lead(resale_value=20.0, current_bid=15.0,
                                   logistics_penalty=15.0, dts=10, target_roi=1.5)
    assert bad.status == "PASS"


def test_illiquid_items_never_gold():
    lead = financials.evaluate_lead(resale_value=500.0, current_bid=1.0,
                                    logistics_penalty=15.0, dts=999, target_roi=1.5)
    assert lead.status == "PASS"


def test_settings_override_beats_env(monkeypatch):
    from app.services import settings as settings_store
    monkeypatch.setattr(settings_store, "get",
                        lambda key: "200" if key == "target_roi_pct" else None)
    assert financials.current_target_roi() == 2.0


def test_settings_missing_falls_back_to_env(monkeypatch):
    from app.services import settings as settings_store
    monkeypatch.setattr(settings_store, "get", lambda key: None)
    assert financials.current_target_roi() == financials.TARGET_ROI


def test_buffer_no_longer_double_counts_packing():
    """Packing moved into the caller's per-tier logistics figure; charging it
    again here put $30 of flat overhead on every item."""
    assert financials.BUFFER == 0.0
    lead = financials.evaluate_lead(resale_value=60.0, current_bid=10.0,
                                    logistics_penalty=3.5, dts=10,
                                    target_roi=1.5, buyers_premium=0.15)
    # hammer 10 x 1.15 x 1.0825, + 3.50 logistics, nothing else
    assert lead.total_cost == round(10 * 1.15 * 1.0825 + 3.5, 2)


def test_tax_compounds_on_the_premium():
    """The invoice taxes hammer + premium, not hammer alone.

    Reconciles to a real settlement: a $690 hammer at 22% premium and 7% tax
    billed $939.72 including $39 of shipping, i.e. $900.72 of lots.
    """
    assert financials.acquisition_multiplier(0.22, 0.07) == pytest.approx(1.3054)
    lots_cost = 690 * financials.acquisition_multiplier(0.22, 0.07)
    assert lots_cost + 39 == pytest.approx(939.72, abs=0.02)
    # The old flat sum understated it — the gap is premium x tax.
    assert lots_cost - 690 * (1 + 0.22 + 0.07) == pytest.approx(690 * 0.22 * 0.07)
