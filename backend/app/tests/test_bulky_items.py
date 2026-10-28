"""Bulky lots are HARD to ship. Pure.

The #660 audit had a weight bench, a wheelchair, a 55" faux plant, a 57"
floor lamp and 92" fishing rods graded NEUTRAL on their titles - gold mines
that no parcel would hold.
"""

import pytest

from app import models
from app.services.hibid import classify_logistics, is_bulky
from app.workers import enrich


@pytest.mark.parametrize("title,description", [
    ("Adjustable Multifunctional Weight Workout Bench",
     "-This lot features a bench. -Dimensions: Approximately 56\" L x 31\" H. "
     "-Material / Brand: Steel frame"),
    ("Vintage Fishing Rods Duo",
     "-Dimensions: Approximately 59\" (4'11\") for the shorter rod and up to "
     "92\" (7'8\") for the longer rod. -Material/Brand: Fiberglass"),
    ("Artificial Faux Bamboo Plant in Glazed Planter",
     "-Dimensions: Approximately 55 inches tall (overall height). -Material: x"),
    ("Drive Medical Cruiser III Folding Wheelchair", ""),
    ("Equate Adjustable Black Folding Walker w/Wheels", ""),
    ("Drive Medical Drop-Arm Commode w/ Padded Seat", ""),
    ("Vintage Metal Torchiere Floor Lamp Glass Shade", ""),
    ("Shed Door", "-Dimensions: Approximately 6 ft tall. -Material: wood"),
])
def test_bulky_lots_are_hard(title, description):
    assert classify_logistics(title, "", description) == "HARD"


@pytest.mark.parametrize("title,description", [
    # Folds flat whatever its packed-out height.
    ("Rightline Gear Hitch Rack Dry Bags Set",
     "-Dimensions: Approximately 30\" W x 20\" D x 55\" H each. -Material/ Brand: x"),
    # A 50 ft coil is not a 50 ft box.
    ("Vigoro 50 FT Heavy-Duty Recycled Landscape Edging",
     "-Dimensions: Approximately 50 ft in length; coiled dimensions are approx. "
     "24\" in diameter x 16\" in height. -Material: plastic"),
    ("Crayola Kids Double-Sided Standing Art Easel",
     "-Dimensions: Approximately 44\" H. -Material: wood"),
    # Size words in the dimensions line aren't measurements.
    ("Vtg Coleman Model 220J Double Mantle Lantern",
     "-Dimensions: Approximately 14\" H x 6.5\" Diameter base. -Material: metal"),
    # Only the dimensions line counts - not a stray number elsewhere.
    ("Bissell Carpet Sweeper", "Fits rooms up to 60\" wide. Works great."),
])
def test_parcel_sized_lots_are_not_bulky(title, description):
    assert not is_bulky(description, title)


def test_regrade_upgrades_old_lots_but_respects_your_tier():
    lot = models.Lot(lot_id="1", title="Drive Medical Cruiser III Folding Wheelchair",
                     description="", logistics_ease="NEUTRAL", source="Local Pickup",
                     current_bid=2, next_bid=2, unreachable_pickup=False,
                     auction=models.Auction(name="a", source="Local Pickup",
                                            buyer_premium_mult=1.18))
    e = models.Enrichment(lot_id=1, user_overrides=[], est_resale=45, comp_count=27,
                          verdict="normal wear and tear", price_source="sold (SoldComps)")
    lot.enrichment = e

    class _Db:
        def refresh(self, _):
            pass

        def commit(self):
            pass

    enrich._regrade_rows(_Db(), [lot])
    assert lot.logistics_ease == "HARD"

    lot.logistics_ease = "NEUTRAL"
    e.user_overrides = ["logistics_ease"]
    enrich._regrade_rows(_Db(), [lot])
    assert lot.logistics_ease == "NEUTRAL"
