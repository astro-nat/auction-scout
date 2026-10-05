"""Gold and silver content read from a lot's own words, and the melt floor. Pure.

Every description below is the Rare Estate house's own wording.
"""

import pytest

from app import models
from app.services import metal
from app.workers import enrich

PRICES = {"gold": 4157.30, "silver": 61.28}      # spot, 2026-10-04


@pytest.mark.parametrize("title,desc,label,grams,metal_grams", [
    ("18K Yellow Gold True Ruby & Diamond Custom Earring",
     "Custom made and designed, total grams are 4.42, fine shape", "18K", 4.42, 3.09),
    ("Rizzo FINE 18K Yellow Gold Woven Style Ring",
     "solid 18K, signed and tested, great shape, size is 7. Grams are 15.70!!! SOLID!!", "18K", 15.70, 15.70),
    ("14K Yellow Gold Antique Tigers Eye Spinner Fob",
     "Signed and tested, fine shape, total grams with stone are 5.89", "14K", 5.89, 4.12),
    ("Vintage Sterling Silver Cuff Bracelet 32.5g", "", "Sterling", 32.5, 32.5),
])
def test_reads_karat_and_grams(title, desc, label, grams, metal_grams):
    info = metal.assess(title, desc)
    assert (info["label"], info["grams"], info["metal_grams"]) == (label, grams, metal_grams)


@pytest.mark.parametrize("title", [
    "Harry Iskin 10K GF Early Panel Bracelet",
    "14K Gold Filled Rope Chain 20in",
    "18K Gold Plated Hoop Earrings",
    "Gold Tone Statement Necklace",
])
def test_no_gold_in_filled_or_plated(title):
    info = metal.assess(title, "total grams are 12.0")
    assert info is None or info["metal"] != "gold"


def test_sterling_and_gold_is_priced_as_silver():
    info = metal.assess("Antique Sterling & 14K Doctors Caduceus Ring", "total grams are 29.37")
    assert info["metal"] == "silver"


def test_melt_value_at_spot():
    info = metal.assess("Rizzo FINE 18K Yellow Gold Woven Style Ring", "Grams are 15.70")
    # 15.70g x 0.75 x $4157.30 / 31.1035g
    assert metal.melt_value(info, PRICES) == pytest.approx(1573.84, abs=0.05)


def test_no_weight_or_no_price_means_no_melt():
    assert metal.melt_value(metal.assess("14K Gold Ring", ""), PRICES) is None
    assert metal.melt_value(metal.assess("14K Gold Ring 3.2g", ""), {}) is None


def test_melt_floors_a_lowballed_resale(monkeypatch):
    """The audit valued a 5.08g 14K bracelet at $45; its gold says ~$350."""
    monkeypatch.setattr(metal, "spot", lambda: PRICES)
    lot = models.Lot(lot_id="58", title="14K Yellow Gold Garnet & Pearl Bracelet",
                     description="Signed and tested, size is 6.5\", total grams are 5.08",
                     logistics_ease="EASY", source="Local Pickup", current_bid=5, next_bid=6,
                     unreachable_pickup=False,
                     auction=models.Auction(name="a", source="Local Pickup", buyer_premium_mult=1.15))
    e = models.Enrichment(lot_id=1, user_overrides=[], est_resale=45, comp_count=5,
                          verdict="normal wear and tear", price_source="sold (SoldComps)")
    lot.enrichment = e
    enrich._apply_roi(lot, e)
    assert e.metal_label == "14K" and e.metal_grams == 5.08
    assert float(e.melt_value) > 200
    assert float(e.max_bid) > 45     # graded from the metal, not the $45
