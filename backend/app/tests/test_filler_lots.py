"""Mixed lots shouldn't rank on a pile of cheap oddments.

Summing every item flatters them badly: thirty things at $12 totals $360,
but that's thirty listings and thirty parcels for $12 apiece.
"""

import pytest

from app import models
from app.services import pricing
from app.workers import enrich


class _FakeDB:
    def commit(self):
        pass


@pytest.fixture
def inspect_with(monkeypatch):
    """Run the itemiser over a fixed set of items, no network.

    monkeypatch rather than plain assignment — these are module globals, and
    overwriting them would leak into every test that runs afterwards.
    """
    def run(items, comp_prices=None):
        prices = comp_prices or {}

        def fake_comps(title):
            if title in prices:
                return {"est_resale": prices[title], "price_low": None,
                        "price_high": None, "comp_count": 4,
                        "price_source": "sold (SoldComps)"}
            return {"est_resale": None, "price_low": None, "price_high": None,
                    "comp_count": 0, "price_source": None}

        monkeypatch.setattr(enrich, "_download_image", lambda *a, **k: b"jpeg")
        monkeypatch.setattr(enrich, "_call_with_retry", lambda fn: {
            "items": items, "summary": "a mixed lot", "ship": "NEUTRAL"})
        monkeypatch.setattr(pricing, "lookup_comps", fake_comps)

        lot = models.Lot(lot_id="1", title="Box of assorted items",
                         logistics_ease="NEUTRAL", source="Local Pickup",
                         current_bid=5, next_bid=6,
                         thumbnail_url="http://example/i.jpg",
                         auction=models.Auction(name="a", source="Local Pickup",
                                                buyer_premium_mult=1.15))
        e = models.Enrichment(lot_id=1, user_overrides=[])
        lot.enrichment = e
        enrich._inspect(lot, e, _FakeDB())
        return e
    return run


def _filler(n, value):
    items = [{"title": f"trinket {i}", "est_value": value + 3} for i in range(n)]
    return items, {f"trinket {i}": float(value) for i in range(n)}


def test_a_crate_of_cheap_oddments_does_not_total_up(inspect_with):
    """Ten $12 items summed naively is $120; as one bundle it's worth ~$24."""
    items, prices = _filler(10, 12)
    e = inspect_with(items, prices)
    assert float(e.est_resale) == pytest.approx(120.0 * enrich.FILLER_REALIZATION)
    assert e.comp_count == 0            # nothing here is worth listing alone


def test_one_real_item_beats_a_pile_of_filler(inspect_with):
    items, prices = _filler(8, 10)
    items = [{"title": "Omega watch", "est_value": 400}] + items
    prices["Omega watch"] = 300.0
    e = inspect_with(items, prices)
    assert float(e.est_resale) == pytest.approx(
        300.0 + 80.0 * enrich.FILLER_REALIZATION)
    assert e.comp_count == 1            # the ROI rests on the one real item


def test_items_on_the_threshold_count_as_sellable(inspect_with):
    e = inspect_with([{"title": "thing", "est_value": 30}],
                     {"thing": enrich.MIN_ITEM_VALUE})
    assert float(e.est_resale) == enrich.MIN_ITEM_VALUE
    assert e.comp_count == 1


def test_the_breakdown_says_what_was_dropped(inspect_with):
    items, prices = _filler(5, 8)
    items = [{"title": "good thing", "est_value": 90}] + items
    prices["good thing"] = 90.0
    e = inspect_with(items, prices)
    assert f"5 filler under ${enrich.MIN_ITEM_VALUE:g}" in e.notes
    assert "worth listing" in e.price_source
    assert "filler bundled" in e.price_source


def test_filler_credit_can_be_switched_off(inspect_with, monkeypatch):
    monkeypatch.setattr(enrich, "FILLER_REALIZATION", 0.0)
    items, prices = _filler(6, 11)
    e = inspect_with(items, prices)
    # Nothing sellable and no bundle credit — the lot is left unpriced rather
    # than given a number nobody would act on.
    assert e.est_resale is None
