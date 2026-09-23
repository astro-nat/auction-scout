"""What the itemized pass does with a lot it has counted. Pure - no network.

The case: a Vinted listing titled just "dvd movies". The enriched title
had invented "Huge Bulk Lot", which matched sold comps for 135-to-650
disc lots at $44 to $205. The inspection then counted exactly ten discs
at $2 each - and threw its own answer away twice. First it ruled the ten
different films a "single product", because the bare source title says
nothing, and took the dearest disc instead of the sum. Then it kept the
$44 as "stronger comps" because seven comps beat zero, with no look at
whether $44 could possibly be right for what it had just counted.

Two rules change. Multi-item is judged from every description on hand -
the enriched title said "Lot", the summary said "collection of assorted".
And a prior price several times a counted multi-item total is read as
comps for a bigger lot: the count stands, and the note says why.
"""

import pytest

from app import models
from app.services import pricing
from app.workers import enrich


class _FakeDB:
    def commit(self):
        pass


@pytest.fixture
def inspect(monkeypatch):
    def run(items, *, title, enriched_title=None, summary="what the photo shows",
            comp_prices=None, prior=None):
        prices = comp_prices or {}

        def fake_comps(t):
            if t in prices:
                return {"est_resale": prices[t], "price_low": None, "price_high": None,
                        "comp_count": 4, "price_source": "sold (SoldComps)"}
            return {"est_resale": None, "price_low": None, "price_high": None,
                    "comp_count": 0, "price_source": None}
        monkeypatch.setattr(enrich, "_download_image", lambda *a, **k: b"jpeg")
        monkeypatch.setattr(enrich, "_call_with_retry", lambda fn: {
            "items": items, "summary": summary, "ship": "NEUTRAL"})
        monkeypatch.setattr(pricing, "lookup_comps", fake_comps)
        lot = models.Lot(lot_id="1", title=title, logistics_ease="NEUTRAL",
                         source="Local Pickup", current_bid=1, next_bid=1,
                         thumbnail_url="http://example/i.jpg",
                         auction=models.Auction(name="a", source="Local Pickup",
                                                buyer_premium_mult=1.15))
        e = models.Enrichment(lot_id=1, user_overrides=[], enriched_title=enriched_title)
        if prior:
            e.est_resale, e.comp_count, e.price_source = prior
        lot.enrichment = e
        enrich._inspect(lot, e, _FakeDB())
        return e
    return run


def _discs(n=10, est_value=3):
    """n different films, each AI-valued under the filler floor."""
    names = ["Joe Dirt", "Slipstream", "Bangkok Dangerous", "School for Scoundrels",
             "Bad Words", "Code Name The Cleaner", "Live-in Maid", "The Other Guys",
             "Semi-Pro", "The Reef"]
    return [{"title": f"{names[i % len(names)]} DVD Disc Only", "est_value": est_value}
            for i in range(n)]


def _bundled(n=10, est_value=3):
    each = round(est_value * enrich.AI_ESTIMATE_REALIZATION, 2)
    return round(n * each * enrich.FILLER_REALIZATION, 2)


def test_ten_counted_discs_beat_bulk_lot_comps(inspect):
    """The production lot, end to end: not a single product, and the $44
    from bulk-lot comps does not survive a count that adds up to $4.80."""
    e = inspect(_discs(), title="dvd movies",
                enriched_title="Huge Bulk Lot DVD Movies Discs Only In Plastic Sleeves",
                summary="A collection of assorted movie DVDs stored loose.",
                prior=(44.09, 7, "sold (SoldComps)"))
    assert "single product" not in e.notes
    assert float(e.est_resale) == pytest.approx(_bundled())
    assert e.price_source.startswith("itemized")
    assert "comps were for a larger lot" in e.notes
    assert "kept pre-inspection" not in e.notes


def test_the_enriched_title_alone_marks_a_multi_item_lot(inspect):
    e = inspect(_discs(), title="dvd movies",
                enriched_title="Lot of DVD Movies", summary="some discs")
    assert "single product" not in e.notes
    assert float(e.est_resale) == pytest.approx(_bundled())


def test_the_summary_alone_marks_a_multi_item_lot(inspect):
    e = inspect(_discs(), title="dvd movies", enriched_title=None,
                summary="A collection of assorted DVDs in sleeves.")
    assert "single product" not in e.notes
    assert float(e.est_resale) == pytest.approx(_bundled())


def test_a_bracelet_split_into_charms_is_still_one_product(inspect):
    """The case the single-product rule exists for, kept: nothing in any
    description says lot, bundle or assorted, so the charms are not summed
    and the best one stands for the bracelet."""
    items = [{"title": f"charm {i}", "est_value": 40} for i in range(7)]
    prices = {f"charm {i}": 30.0 + i for i in range(7)}          # best = $36
    e = inspect(items, title="Mexico Sterling Charm Bracelet",
                enriched_title="Vintage Mexico Sterling Silver Charm Bracelet",
                summary="a sterling charm bracelet with seven charms",
                comp_prices=prices)
    assert "single product" in e.notes
    assert float(e.est_resale) == pytest.approx(36.0)


def test_a_prior_within_reason_is_still_kept(inspect):
    """Seven real comps at a plausible price still outrank a count of AI
    guesses. The override is for comps that cannot be for this lot, not
    for every disagreement."""
    e = inspect(_discs(), title="dvd movies",
                enriched_title="Lot of DVD Movies", summary="assorted discs",
                prior=(8.00, 7, "sold (SoldComps)"))
    assert float(e.est_resale) == pytest.approx(8.00)
    assert "kept pre-inspection price" in e.notes


def test_a_single_product_never_triggers_the_count_override(inspect):
    """A wrong-size comp set is a multi-item problem. A single item whose
    comps beat the AI's guess keeps the comps, whatever the ratio."""
    items = [{"title": f"charm {i}", "est_value": 5} for i in range(7)]
    e = inspect(items, title="Mexico Sterling Charm Bracelet",
                summary="a charm bracelet", prior=(120.0, 9, "sold (SoldComps)"))
    assert float(e.est_resale) == pytest.approx(120.0)
    assert "kept pre-inspection price" in e.notes
