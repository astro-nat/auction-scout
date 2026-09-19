"""House estimates cap weak-evidence values — never real market data.

The house's estimate range is promotional, but a house rarely lowballs its
own consignment, so ESTIMATE_REALIZATION × the low end is a credible
ceiling. Strong sold comps and retail-in-title prices stand as-is: market
data beats the house's guess in either direction.
"""

import pytest

from app import models
from app.services.hibid import parse_estimate
from app.workers import enrich


@pytest.mark.parametrize("raw,expected", [
    ("850.00 - 1,500.00 USD", (850.0, 1500.0)),
    ("100.00 - 200.00 USD", (100.0, 200.0)),
    ("75 USD", (75.0, 75.0)),
    ("1,500.00 - 850.00 USD", (850.0, 1500.0)),   # swapped ends normalize
    ("", (None, None)),
    (None, (None, None)),
    ("TBD", (None, None)),
    ("0 - 0 USD", (None, None)),                   # zero is not an estimate
])
def test_parse_estimate(raw, expected):
    assert parse_estimate(raw) == expected


def _lot(est_low, resale, price_source, comp_count, overrides=None):
    lot = models.Lot(lot_id="1", title="Cuff", estimate_low=est_low,
                     estimate_high=(est_low * 2 if est_low else None))
    e = models.Enrichment(lot_id=1, est_resale=resale, comp_count=comp_count,
                          price_source=price_source,
                          user_overrides=overrides or [])
    lot.enrichment = e
    return lot, e


def test_weak_evidence_is_capped_at_80pct_of_house_low():
    lot, e = _lot(100, 240, "active (eBay) ×0.65 asking→sold", 5)
    enrich._apply_estimate_cap(lot, e)
    assert float(e.est_resale) == pytest.approx(100 * enrich.ESTIMATE_REALIZATION)
    assert "capped" in e.price_source and "house-low" in e.price_source


def test_thin_sold_comps_are_capped_too():
    lot, e = _lot(100, 240, "sold (SoldComps)", 2)   # only 2 comps
    enrich._apply_estimate_cap(lot, e)
    assert float(e.est_resale) == pytest.approx(80.0)


def test_strong_sold_comps_beat_the_house_guess():
    lot, e = _lot(100, 240, "sold (SoldComps)", 8)
    enrich._apply_estimate_cap(lot, e)
    assert float(e.est_resale) == 240
    assert "capped" not in (e.price_source or "")


def test_retail_in_title_is_not_capped():
    lot, e = _lot(100, 240, "retail $480 in title ×0.5", 1)
    enrich._apply_estimate_cap(lot, e)
    assert float(e.est_resale) == 240


def test_values_under_the_cap_pass_untouched():
    lot, e = _lot(100, 60, "active (eBay) ×0.65 asking→sold", 3)
    enrich._apply_estimate_cap(lot, e)
    assert float(e.est_resale) == 60
    assert "capped" not in (e.price_source or "")


def test_no_estimate_means_no_cap():
    lot, e = _lot(None, 240, "active (eBay) ×0.65 asking→sold", 3)
    enrich._apply_estimate_cap(lot, e)
    assert float(e.est_resale) == 240


def test_hand_set_prices_are_never_capped():
    lot, e = _lot(100, 240, "active (eBay)", 3, overrides=["est_resale"])
    enrich._apply_estimate_cap(lot, e)
    assert float(e.est_resale) == 240
