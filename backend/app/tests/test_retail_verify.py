"""Expensive retail-in-title claims get a market cross-check.

The incident: a "$556 retail" Logitech racing wheel — used market ~$130 —
was priced at $278 and badged GOLD MINE with a $111 max bid, because a
printed MSRP was treated as an appraisal. The claim is a data point; when
real money is at stake the market gets a vote, and the lower answer wins.
Cheap claims stay on the zero-cost path untouched.
"""

import pytest

from app import models
from app.services import pricing
from app.workers import enrich


def _market(est, count=6, comps=None):
    return {"est_resale": est, "price_low": est and est * 0.8,
            "price_high": est and est * 1.2, "comp_count": count if est else 0,
            "price_source": "sold (SoldComps)" if est else None,
            "comps": comps or []}


def test_cheap_claims_never_touch_the_network(monkeypatch):
    monkeypatch.setattr(pricing, "lookup_comps",
                        lambda *a, **k: pytest.fail("comp lookup on a cheap claim"))
    r = pricing.verified_title_price("$120 Widget Pro 3000 Cordless Drill")
    assert r["est_resale"] == 60.0
    assert r["price_source"] == "retail $120 in title ×0.5"


def test_inflated_claim_loses_to_the_market(monkeypatch):
    seen = {}

    def fake_lookup(query):
        seen["query"] = query
        return _market(130.0)

    monkeypatch.setattr(pricing, "lookup_comps", fake_lookup)
    r = pricing.verified_title_price(
        "$556 Logitech G29 Driving Force Racing Wheel for PS5")
    assert r["est_resale"] == 130.0                 # market wins
    assert "beat the retail $556 claim" in r["price_source"]
    assert "$556" not in seen["query"]              # searched the item, not the price


def test_honest_claim_survives_the_check(monkeypatch):
    monkeypatch.setattr(pricing, "lookup_comps", lambda q: _market(400.0))
    r = pricing.verified_title_price("$556 Fancy Widget Deluxe Edition Set")
    assert r["est_resale"] == 278.0                 # claim x0.5 already lower
    assert "(market-checked)" in r["price_source"]


def test_no_market_data_leaves_the_claim_labeled(monkeypatch):
    monkeypatch.setattr(pricing, "lookup_comps", lambda q: _market(None))
    r = pricing.verified_title_price("$556 Obscure Widget Nobody Sells")
    assert r["est_resale"] == 278.0
    assert "(no comps to check the claim)" in r["price_source"]


def test_market_evidence_rides_along_when_it_wins(monkeypatch):
    comps = [{"price": 135.0, "title": "G29 wheel used", "url": None,
              "date": None, "kind": "sold"}]
    monkeypatch.setattr(pricing, "lookup_comps",
                        lambda q: _market(130.0, comps=comps))
    r = pricing.verified_title_price("$556 Logitech G29 Racing Wheel")
    assert r["comps"] == comps                      # the homework transfers


class _FakeDB:
    def commit(self):
        pass


def test_enrich_still_free_for_cheap_titled_lots(monkeypatch):
    def boom(*a, **kw):
        raise AssertionError("spent on a cheap titled lot")
    monkeypatch.setattr(enrich, "_call_text", boom)
    monkeypatch.setattr(enrich, "_call_vision", boom)
    monkeypatch.setattr(enrich, "_download_image", boom)
    monkeypatch.setattr(pricing, "lookup_comps", boom)

    lot = models.Lot(lot_id="1", title="$120 Widget Pro 3000 Cordless Drill",
                     logistics_ease="EASY", source="Ship",
                     current_bid=5, next_bid=6,
                     auction=models.Auction(name="a", source="Ship",
                                            buyer_premium_mult=1.18))
    e = models.Enrichment(lot_id=1, user_overrides=[])
    lot.enrichment = e
    enrich._enrich(lot, e, _FakeDB())
    assert e.est_resale == 60.0
    assert e.ai_source == "title"


def test_enrich_caps_the_wheel(monkeypatch):
    """The incident, end to end: expensive claim, market says less, PASS."""
    def boom(*a, **kw):
        raise AssertionError("model call on a titled lot")
    monkeypatch.setattr(enrich, "_call_text", boom)
    monkeypatch.setattr(enrich, "_call_vision", boom)
    monkeypatch.setattr(enrich, "_download_image", boom)
    monkeypatch.setattr(pricing, "lookup_comps", lambda q: _market(130.0))

    lot = models.Lot(lot_id="1",
                     title="$556 Logitech G29 Driving Force Racing Wheel",
                     logistics_ease="EASY", source="Ship",
                     current_bid=5, next_bid=6,
                     auction=models.Auction(name="a", source="Ship",
                                            buyer_premium_mult=1.18))
    e = models.Enrichment(lot_id=1, user_overrides=[])
    lot.enrichment = e
    enrich._enrich(lot, e, _FakeDB())
    assert float(e.est_resale) == 130.0
    assert "beat the retail $556 claim" in e.price_source