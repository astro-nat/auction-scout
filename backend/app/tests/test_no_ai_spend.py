"""A lot whose title carries its retail price must cost nothing at the model.

The catalogues this matters for run 700-2400 lots; a stray vision call per
lot is the whole point of the feature.
"""

import pytest

from app import models
from app.services import pricing
from app.workers import enrich


@pytest.fixture
def no_model_calls(monkeypatch):
    """Turn every paid MODEL path into a test failure.

    Comp lookups are not model spend: retail claims at or above
    RETAIL_VERIFY_MIN legitimately cross-check the market now (a "$556
    retail" wheel with a ~$130 used market was minting false golds). Here
    the market has no data, so every claim stands — and test_retail_verify
    pins that CHEAP claims still skip the lookup entirely."""
    def boom(*a, **kw):
        raise AssertionError("spent money on a lot that was already priced")
    monkeypatch.setattr(enrich, "_call_text", boom)
    monkeypatch.setattr(enrich, "_call_vision", boom)
    monkeypatch.setattr(enrich, "_download_image", boom)
    monkeypatch.setattr(pricing, "lookup_comps", lambda *a, **k: {
        "est_resale": None, "price_low": None, "price_high": None,
        "comp_count": 0, "price_source": None, "comps": []})


class _FakeDB:
    def commit(self):
        pass


def _run(title, description=""):
    lot = models.Lot(lot_id="1", title=title, description=description,
                     logistics_ease="EASY", source="Ship",
                     current_bid=5, next_bid=6,
                     auction=models.Auction(name="a", source="Ship",
                                            ship_cost_estimate=12.0,
                                            buyer_premium_mult=1.18))
    e = models.Enrichment(lot_id=1, user_overrides=[])
    lot.enrichment = e
    enrich._enrich(lot, e, _FakeDB())
    return lot, e


def test_titled_lot_never_calls_the_model(no_model_calls):
    lot, e = _run("New $122 Suptek 3-Shelf Bracket, Tempered Glass",
                  description="x" * 500)   # long enough to tempt the text pass
    assert e.ai_source == "title"
    assert e.est_resale == 61.0            # half of $122
    assert e.enriched_title == "Suptek 3-Shelf Bracket, Tempered Glass"


def test_long_description_does_not_trigger_the_text_pass(no_model_calls):
    _, e = _run("$40 Everjoys Soprano Ukulele Beginner Kit", "y" * 2000)
    assert e.ai_source == "title"


def test_missing_photo_does_not_trigger_the_vision_pass(no_model_calls):
    _, e = _run("$56 in-Channel Window Rain Guards")
    assert e.ai_source == "title"


def test_condition_comes_from_the_houses_own_stamp(no_model_calls):
    _, e = _run("MISSING $178 VEVOR Truck Bed Cover")
    assert e.verdict == "broken, damaged, or for parts"
    # 178/2 = 89, then the damaged multiplier
    assert e.est_resale == pytest.approx(89.0 * 0.25)


def test_sealed_reads_as_mint(no_model_calls):
    _, e = _run("SEALED $100 Raised Garden Bed")
    assert e.verdict == "mint condition or working perfectly"
    assert e.est_resale == 50.0            # mint multiplier is 1.0


def test_plain_titled_lot_is_exactly_half(no_model_calls):
    _, e = _run("$30 Hagerty Flatware Silver Dip")
    assert e.verdict == "normal wear and tear"
    assert e.est_resale == 15.0


def test_titled_lot_can_still_be_gold(no_model_calls):
    """comp_count is 1, which normally forces PASS — a printed retail price
    is the documented exception."""
    _, e = _run("$900 HP LaserJet Pro 500 Color MFP M570DN")
    assert e.roi_status == "GOLD MINE"


def test_untitled_lot_still_goes_to_the_model(monkeypatch):
    """The saving must not leak into ordinary lots."""
    called = []
    monkeypatch.setattr(enrich, "_call_text",
                        lambda *a, **k: called.append("text") or
                        {"enriched_title": "Widget", "verdict": "normal wear and tear",
                         "confident": True, "notes": "n", "ship": "EASY"})
    monkeypatch.setattr(pricing, "lookup_comps",
                        lambda *a, **k: {"est_resale": 10, "price_low": 8,
                                         "price_high": 12, "comp_count": 4,
                                         "price_source": "sold"})
    _run("Vintage Brass Candlestick Holder Pair", "z" * 500)
    assert called == ["text"]
