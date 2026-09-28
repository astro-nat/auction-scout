"""The HARD freight floor: a stand-in for not knowing, not a verdict. Pure.

A HARD lot doesn't go in a box, and no multiple of a house's shoebox rate is
an LTL freight quote - 4x a $12 quote is $48 against $150-400 of real
freight. That gap manufactured gold mines (52 of 62 in one industrial sale,
including a ceiling-mounted air purification station), which is why the floor
exists.

But it was also overriding the one thing that could improve on it. The
shipping read asks for a small-item rate by definition, so reading a house's
terms could never change a HARD lot's cost: max(anything sane, 150) is 150.
Now the read asks what an OVERSIZED lot costs too, and when the house says,
that figure wins.
"""

import pytest

from app.workers.enrich import (DEFAULT_INBOUND_SHIP, FREIGHT_FLOOR,
                                INBOUND_TIER_MULT, _house_freight,
                                _inbound_shipping)

FLOOR = FREIGHT_FLOOR["HARD"]


class _Auction:
    def __init__(self, parcel=None, freight=None, source="Ship"):
        self.ship_cost_estimate = parcel
        self.ship_freight_estimate = freight
        self.source = source


class _Lot:
    def __init__(self, ease, auction=None, source="Ship"):
        self.logistics_ease = ease
        self.auction = auction
        self.source = source


# --- what the floor is for ------------------------------------------------

def test_an_unread_house_still_gets_the_floor():
    """Nothing known about this sale: the conservative number stands."""
    lot = _Lot("HARD", _Auction())
    assert _inbound_shipping(lot) == FLOOR


def test_a_small_parcel_rate_alone_does_not_price_a_pallet():
    """The old behaviour, deliberately kept: reading "$12 a box" tells you
    nothing about a freight lot, so it must not talk the cost down."""
    lot = _Lot("HARD", _Auction(parcel=12.0))
    assert _inbound_shipping(lot) == FLOOR


# --- what it was getting wrong -------------------------------------------

def test_a_real_freight_quote_beats_the_floor():
    """The fix. A house that says oversized items run $300 is telling you
    something the floor was invented to substitute for."""
    lot = _Lot("HARD", _Auction(parcel=12.0, freight=300.0))
    assert _inbound_shipping(lot) == 300.0


def test_a_real_freight_quote_below_the_floor_is_still_used():
    """The point of reading is to learn. A house that crates and ships for
    $90 is cheaper than the guess, and pretending otherwise hides lots that
    are genuinely worth bidding on."""
    lot = _Lot("HARD", _Auction(parcel=12.0, freight=90.0))
    assert _inbound_shipping(lot) == 90.0


def test_a_freight_quote_takes_no_tier_multiplier():
    """It is already the oversized price - multiplying it by the HARD tier
    would charge for the bulk twice."""
    lot = _Lot("HARD", _Auction(parcel=12.0, freight=200.0))
    assert _inbound_shipping(lot) == 200.0
    assert _inbound_shipping(lot) != 200.0 * INBOUND_TIER_MULT["HARD"]


# --- the guard on a misread ----------------------------------------------

def test_freight_cheaper_than_a_shoebox_is_a_misread():
    """Freight cannot cost less than a parcel from the same house. Checking
    the house's quote against its own other quote beats inventing a second
    threshold - and a misread that slips through is how a pallet becomes a
    gold mine."""
    assert _house_freight(_Auction(parcel=20.0, freight=8.0), 20.0) is None
    lot = _Lot("HARD", _Auction(parcel=20.0, freight=8.0))
    assert _inbound_shipping(lot) == FLOOR


def test_a_freight_figure_equal_to_the_parcel_rate_is_allowed():
    """A flat-rate house really can charge the same either way; only LESS is
    impossible."""
    assert _house_freight(_Auction(parcel=40.0, freight=40.0), 40.0) == 40.0


def test_a_missing_or_zero_freight_figure_falls_back():
    for bad in (None, 0, 0.0):
        assert _house_freight(_Auction(parcel=12.0, freight=bad), 12.0) is None


def test_the_fallback_compares_against_the_default_when_the_house_is_unread():
    """With no small-parcel figure either, the default stands in as the
    thing freight has to beat."""
    lot = _Lot("HARD", _Auction(freight=DEFAULT_INBOUND_SHIP - 1))
    assert _inbound_shipping(lot) == FLOOR


# --- everything else is untouched ----------------------------------------

@pytest.mark.parametrize("tier", ["EASY", "NEUTRAL"])
def test_a_freight_quote_does_not_touch_a_parcel_lot(tier):
    """A shoebox from a house that also does pallets is still a shoebox."""
    lot = _Lot(tier, _Auction(parcel=12.0, freight=300.0))
    assert _inbound_shipping(lot) == round(12.0 * INBOUND_TIER_MULT[tier], 2)


def test_local_pickup_still_costs_nothing():
    lot = _Lot("HARD", _Auction(parcel=12.0, freight=300.0), source="HiBid pickup")
    assert _inbound_shipping(lot) == 0.0


def test_a_lot_with_no_auction_at_all_is_priced_not_crashed():
    assert _inbound_shipping(_Lot("HARD", None)) == FLOOR
    assert _inbound_shipping(_Lot("NEUTRAL", None)) == round(
        DEFAULT_INBOUND_SHIP * INBOUND_TIER_MULT["NEUTRAL"], 2)
