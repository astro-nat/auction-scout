"""What fits the poly mailer. Pure."""

import pytest

from app import models
from app.services import mailer
from app.workers import enrich

SIZE = (14.5, 20.0)


@pytest.mark.parametrize("title,description,tier,lb,fits", [
    # Soft and flat goods always fit, jackets and shoes included.
    ("Reebok Dwyane Wade Miami Heat #3 Jersey 3XL", "", "NEUTRAL", 1.5, True),
    ("North Face Puffer Jacket Mens XL", "", "NEUTRAL", 3, True),
    ("Nike Air Jordan 1 Sneakers Size 10", "", "NEUTRAL", 3, True),
    ("Signed Kenneth Lane 36\" Faceted Necklace", "", "NEUTRAL", 0.2, True),
    ("Vintage January 1939 UAW-CIO Union Pinback Button", "", "NEUTRAL", 0.2, True),
    # ...but not when the title is really furniture or a machine.
    ("Wooden Hat Rack", "", "NEUTRAL", 5, False),
    ("Emerson NR303TT Wooden Record Player & Radio", "", "NEUTRAL", 10, False),
    ("Framed Casablanca French Movie Poster", "", "NEUTRAL", 8, False),
    # Measured: length + thickness within 20, width + thickness within 14.5.
    ("Vtg Coleman Model 220J Double Mantle Lantern",
     "-Dimensions: Approximately 14\" H x 6.5\" Diameter base. -Material: metal",
     "NEUTRAL", 4, False),
    ("Crayola Kids Double-Sided Standing Art Easel",
     "-Dimensions: Approximately 44\" H. -Material: wood", "NEUTRAL", 5, False),
    ("Leather Journal", "-Dimensions: Approximately 9\" x 6\" x 1\". -Material: x",
     "NEUTRAL", 1, True),
    # Fractions: "6-3/8" is six and three eighths, not 8.
    ("Greenlee 35713 6-3/8\" Recessed Light Hole Saw",
     "-Dimensions: Approximately 6-3/8\" (162 mm) diameter; 7/8\" (22.2 mm) cutting depth. -Material: Steel",
     "NEUTRAL", 1.5, True),
    # A hitch rack is what the bags fit.
    ("Rightline Gear Hitch Rack Dry Bags Set",
     "-Dimensions: Approximately 30\" W x 20\" D x 55\" H each. -Material/ Brand: x",
     "NEUTRAL", 1.5, True),
    ("Golfmate Jr. Youth Stand Golf Bag", "", "HARD", 8, False),
    # No size given: hard-to-ship never fits, light items do, heavy don't.
    ("Drive Medical Cruiser III Folding Wheelchair", "", "HARD", 40, False),
    ("Swarovski Kris Bear Lucky Charm Figurine", "", "NEUTRAL", 0.5, True),
    ("Pampered Chef 1-Quart Enameled Cast Iron Baker", "", "NEUTRAL", 7, False),
    ("Mystery Item", "", "NEUTRAL", None, False),
])
def test_fits(title, description, tier, lb, fits):
    assert mailer.fits(title, description, tier, lb, SIZE) is fits


def test_a_bigger_mailer_fits_more():
    lantern = "-Dimensions: Approximately 14\" H x 6.5\" Diameter base. -Material: metal"
    assert not mailer.fits("Coleman Lantern", lantern, "NEUTRAL", 4, (14.5, 20))
    assert mailer.fits("Coleman Lantern", lantern, "NEUTRAL", 4, (16, 24))


@pytest.mark.parametrize("text,size", [
    ("14.5x20", (14.5, 20.0)), ("20 x 14.5", (14.5, 20.0)), ("12 by 15.5", (12.0, 15.5)),
    ("14.5", None), ("1x20", None), ("big", None), ("", None),
])
def test_parse_size(text, size):
    assert mailer.parse_size(text) == size


def test_a_lot_that_wont_fit_is_never_gold(monkeypatch):
    monkeypatch.setattr(mailer, "current_size", lambda: SIZE)
    lot = models.Lot(lot_id="1", title="Pampered Chef 1-Quart Enameled Cast Iron Baker",
                     description="", logistics_ease="NEUTRAL", source="Ship",
                     current_bid=2, next_bid=2, unreachable_pickup=False,
                     auction=models.Auction(name="a", source="Ship", buyer_premium_mult=1.18))
    e = models.Enrichment(lot_id=1, user_overrides=[], est_resale=40, comp_count=5,
                          verdict="normal wear and tear", price_source="sold (SoldComps)")
    lot.enrichment = e
    enrich._apply_roi(lot, e)
    assert e.roi_status == "PASS"
    assert e.roi_reason == "won't fit your 14.5 x 20 in mailer"
