"""Sell-through rate on gold mines. Pure - eBay is stubbed.

Both sides count the same item: the lot's own relevant sold comps, and
eBay's active listings that pass pricing's relevance checks. Two earlier
versions read SoldComps' raw totals and compared different things (0% for
65 gold mines, then a 92% median).
"""

from datetime import datetime, timedelta, timezone

import pytest

from app import models
from app.services import ebay, sell_through
from app.workers import enrich

TITLE = "Vtg Coleman Model 220J Double Mantle Lantern"
SOLD = [{"price": 40, "title": "Coleman 220J lantern", "kind": "sold"}]


@pytest.mark.parametrize("sold,active,pct", [
    (120, 80, 60.0), (0, 50, 0.0), (30, 0, 100.0), (0, 0, None), (None, 5, None),
])
def test_rate(sold, active, pct):
    assert sell_through.rate(sold, active) == pct


@pytest.mark.parametrize("count,comps,sold", [
    (33, SOLD, 33),
    (1, [{"price": 219, "title": "retail price printed in the lot title", "kind": "retail"}], None),
    (8, [{"price": 30, "title": "x", "kind": "asking"}], None),
    (0, SOLD, None),
])
def test_the_sold_side_is_the_lots_own_sold_comps(count, comps, sold):
    assert sell_through.sold_side(count, comps) == sold


def test_the_listed_side_counts_only_the_same_item(monkeypatch):
    monkeypatch.setattr(ebay, "enabled", lambda: True)
    monkeypatch.setattr(ebay, "search_active", lambda q, limit=20: [
        {"title": "Vintage Coleman 220J Double Mantle Lantern Green"},
        {"title": "Coleman 220J Double Mantle Lantern 1978 w/ Box"},
        {"title": "Coleman 228H Lantern"},                 # another model
        {"title": "Mantles for Coleman Lanterns 10 pack"},
    ])
    assert sell_through.listed_side(TITLE, "Coleman 220J Double Mantle Lantern") == 2


def test_no_ebay_means_no_figure(monkeypatch):
    monkeypatch.setattr(ebay, "enabled", lambda: False)
    assert sell_through.listed_side(TITLE, "Coleman 220J lantern") is None


def test_the_listed_side_searches_with_the_comps_wording(monkeypatch):
    """v2 always searched the most specific wording - artist and all - and
    found nothing listed: 16 sold against 0 read as 100%."""
    asked = []
    monkeypatch.setattr(ebay, "enabled", lambda: True)
    monkeypatch.setattr(ebay, "search_active", lambda q, limit=20: asked.append(q) or [])
    sell_through.listed_side(TITLE, "Coleman 220J lantern")
    assert asked == ["Coleman 220J lantern"]


def test_older_lots_get_their_wording_worked_out_from_their_comps():
    from app.services import pricing
    variants = pricing.query_variants(TITLE)
    comps = [{"title": "Coleman 220J lantern green", "kind": "sold"}] * 3
    got = sell_through.comp_wording(TITLE, comps)
    assert got in variants
    assert sum(pricing._relevant(got, c["title"]) for c in comps) * 2 >= len(comps)
    assert sell_through.comp_wording(TITLE, []) is None


class _Db:
    def commit(self):
        pass


def _gold(status="GOLD MINE", checked=None, method=sell_through.METHOD, comps=SOLD, count=33):
    lot = models.Lot(lot_id="1", title=TITLE)
    e = models.Enrichment(lot_id=1, roi_status=status, str_checked_at=checked,
                          str_method=method if checked else None,
                          enriched_title=None, comps=comps, comp_count=count)
    return lot, e


def _listed(monkeypatch, n):
    monkeypatch.setattr(sell_through, "listed_side", lambda *a: n)


def test_a_gold_mine_gets_checked(monkeypatch):
    _listed(monkeypatch, 22)
    lot, e = _gold()
    assert enrich._check_str(_Db(), lot, e) is True
    assert (e.str_sold, e.str_active, e.str_pct, e.str_method) == (33, 22, 60.0, sell_through.METHOD)


def test_only_gold_mines_are_checked(monkeypatch):
    monkeypatch.setattr(sell_through, "listed_side", lambda *a: pytest.fail("checked a PASS"))
    lot, e = _gold(status="PASS")
    assert enrich._check_str(_Db(), lot, e) is False


def test_a_retail_priced_lot_gets_no_figure(monkeypatch):
    monkeypatch.setattr(sell_through, "listed_side", lambda *a: pytest.fail("asked eBay for nothing"))
    lot, e = _gold(comps=[{"price": 219, "title": "retail", "kind": "retail"}], count=1)
    assert enrich._check_str(_Db(), lot, e) is True
    assert e.str_pct is None and e.str_checked_at is not None   # checked, not re-asked


def test_an_ebay_failure_leaves_it_due(monkeypatch):
    _listed(monkeypatch, None)
    lot, e = _gold()
    assert enrich._check_str(_Db(), lot, e) is False
    assert e.str_checked_at is None


def test_a_fresh_check_is_not_repeated(monkeypatch):
    monkeypatch.setattr(sell_through, "listed_side", lambda *a: pytest.fail("rechecked"))
    lot, e = _gold(checked=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=2))
    assert enrich._check_str(_Db(), lot, e) is False


def test_a_week_old_check_is_redone(monkeypatch):
    _listed(monkeypatch, 1)
    lot, e = _gold(checked=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=8))
    assert enrich._check_str(_Db(), lot, e) is True


def test_a_reading_from_an_older_method_is_redone(monkeypatch):
    _listed(monkeypatch, 5)
    lot, e = _gold(checked=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1),
                   method=None)
    e.str_sold, e.str_active, e.str_pct = 240, 24, 90.9
    assert enrich._check_str(_Db(), lot, e) is True
    assert (e.str_sold, e.str_active) == (33, 5)


def test_pricing_saves_the_wording_that_found_the_comps(monkeypatch):
    from app.services import pricing
    sold = [{"price": 40.0 + i, "title": "Coleman 220J Double Mantle Lantern", "kind": "sold"}
            for i in range(8)]
    asked = []
    monkeypatch.setattr(pricing, "_soldcomps_lookup", lambda q, count=120: asked.append(q) or sold)
    result = pricing.lookup_comps(TITLE)
    assert result["comp_query"] == asked[-1]
