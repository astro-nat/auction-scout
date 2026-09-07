"""ROI math — pure functions, explicit targets so the settings store isn't hit."""

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
