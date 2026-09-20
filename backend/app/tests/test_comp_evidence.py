"""The evidence rows behind est_resale: lookup_comps keeps the comp records
it actually used, raw and attributable, alongside the aggregates it always
returned. Pure — the lookups are faked at the source-function level."""

from app.services import pricing


def _sold(price, title, url=None, date=None):
    return {"price": price, "title": title, "url": url, "date": date,
            "kind": "sold"}


def test_comps_ride_along_with_the_estimate(monkeypatch):
    records = [
        _sold(40.0, "Widget Pro 3000", "https://ebay.com/itm/1", "2026-09-01"),
        _sold(45.0, "Widget Pro 3000 mint", "https://ebay.com/itm/2", "2026-08-20"),
        _sold(38.0, "Widget Pro 3000 used", None, "2026-08-02"),
        _sold(42.0, "Widget Pro 3000 boxed", "https://ebay.com/itm/4", None),
    ]
    monkeypatch.setattr(pricing, "_soldcomps_lookup", lambda q, count=120: list(records))
    monkeypatch.setattr(pricing, "query_variants", lambda t: ["widget pro 3000"])
    monkeypatch.setattr(pricing, "_relevant", lambda q, t: True)

    r = pricing.lookup_comps("Widget Pro 3000")
    assert r["est_resale"] == 41.0                 # median unchanged by the feature
    assert r["comp_count"] == 4
    assert len(r["comps"]) == 4
    assert r["comps"][0]["price"] == 45.0          # sorted, priciest first
    assert r["comps"][0]["url"] == "https://ebay.com/itm/2"
    assert r["comps"][0]["kind"] == "sold"
    assert r["comps"][3]["url"] is None            # a linkless comp still shows


def test_iqr_outlier_is_dropped_from_evidence_too(monkeypatch):
    """A comp the fences rejected must not appear as supporting evidence —
    showing it would be showing homework the answer didn't use."""
    records = [_sold(p, f"widget {p}") for p in (40, 41, 42, 43, 44, 400)]
    monkeypatch.setattr(pricing, "_soldcomps_lookup", lambda q, count=120: list(records))
    monkeypatch.setattr(pricing, "query_variants", lambda t: ["widget"])
    monkeypatch.setattr(pricing, "_relevant", lambda q, t: True)

    r = pricing.lookup_comps("widget")
    prices = [c["price"] for c in r["comps"]]
    assert 400 not in prices
    assert r["comp_count"] == len(prices) == 5


def test_stored_evidence_is_capped_and_raw(monkeypatch):
    """Only _COMPS_STORED records survive, and asking-price comps keep their
    observed price even though the estimate is realization-discounted."""
    records = [{"price": float(p), "title": f"widget {p}", "url": None,
                "date": None, "kind": "asking"} for p in range(20, 60, 2)]
    monkeypatch.setattr(pricing, "_soldcomps_lookup", lambda q, count=120: [])
    monkeypatch.setattr(pricing, "_active_lookup", lambda q: list(records))
    monkeypatch.setattr(pricing, "query_variants", lambda t: ["widget"])
    monkeypatch.setattr(pricing, "_relevant", lambda q, t: True)

    r = pricing.lookup_comps("widget")
    assert len(r["comps"]) == pricing._COMPS_STORED
    assert all(c["kind"] == "asking" for c in r["comps"])
    # est_resale carries the asking->sold discount; the evidence does not.
    assert "asking→sold" in r["price_source"]
    assert r["comps"][0]["price"] == 58.0          # raw observed, undiscounted


def test_retail_in_title_documents_itself():
    # Leading sticker price, the liquidation-house style the matcher expects.
    r = pricing.price_from_title("$120 Widget Pro 3000 Cordless Drill Kit")
    assert r is not None
    assert len(r["comps"]) == 1
    assert r["comps"][0]["kind"] == "retail"
    assert r["comps"][0]["price"] == 120.0


def test_thin_comps_partial_still_carries_its_evidence(monkeypatch):
    """The thin-comps path (fewer than the full-comp minimum) prices off a
    partial set — the user distrusts exactly these, so the evidence rows
    matter most here."""
    records = [_sold(40.0, "rare widget", "https://ebay.com/itm/9", "2026-09-05")]
    monkeypatch.setattr(pricing, "_soldcomps_lookup", lambda q, count=120: list(records))
    monkeypatch.setattr(pricing, "_active_lookup", lambda q: [])
    monkeypatch.setattr(pricing, "query_variants", lambda t: ["rare widget"])
    monkeypatch.setattr(pricing, "_relevant", lambda q, t: True)

    r = pricing.lookup_comps("rare widget")
    assert "thin comps" in r["price_source"]
    assert [c["url"] for c in r["comps"]] == ["https://ebay.com/itm/9"]


def test_record_fences_match_the_price_fences():
    """_iqr_records must keep exactly the prices _iqr_filter keeps — if the
    two ever disagree, the stored evidence stops matching the estimate."""
    for prices in ([40.0], [40, 41, 42], [40, 41, 42, 43, 44, 400],
                   [5, 5, 5, 5, 250], [10, 20, 30, 40, 50, 60, 70]):
        records = [_sold(float(p), f"w {p}") for p in prices]
        kept = sorted(c["price"] for c in pricing._iqr_records(records))
        assert kept == sorted(pricing._iqr_filter([float(p) for p in prices]))


def test_variance_cap_shrinks_the_estimate_not_the_evidence(monkeypatch):
    """A wild spread caps the median, but the comps that showed the spread
    stay on display — they are WHY the cap fired."""
    records = [_sold(float(p), f"widget {p}") for p in (10, 11, 80, 90, 100)]
    monkeypatch.setattr(pricing, "_soldcomps_lookup", lambda q, count=120: list(records))
    monkeypatch.setattr(pricing, "query_variants", lambda t: ["widget"])
    monkeypatch.setattr(pricing, "_relevant", lambda q, t: True)

    r = pricing.lookup_comps("widget")
    assert "(variance-capped)" in r["price_source"]
    assert r["est_resale"] < 80                    # median clamped hard
    assert len(r["comps"]) == 5                    # evidence intact
    assert max(c["price"] for c in r["comps"]) == 100.0


def test_no_comps_means_an_empty_list_not_a_missing_key(monkeypatch):
    monkeypatch.setattr(pricing, "_soldcomps_lookup", lambda q, count=120: [])
    monkeypatch.setattr(pricing, "_active_lookup", lambda q: [])
    monkeypatch.setattr(pricing, "query_variants", lambda t: ["widget"])
    r = pricing.lookup_comps("widget")
    assert r["est_resale"] is None
    assert r["comps"] == []
