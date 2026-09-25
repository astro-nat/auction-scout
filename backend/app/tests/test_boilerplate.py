"""Telling the house's notices from its lots. Pure.

Every title in both lists is a real one from the inventory. The notices
were being imported, comped and valued - $56.70 for a returns policy,
$43.11 for pickup hours - and then hidden by hand.
"""

from app.services.boilerplate import is_boilerplate

NOTICES = [
    "CLOSING TIME - Friday, 6:30 PM CST",
    "Auction Notice - Please Read",
    "PLEASE READ BEFORE BIDDING By bidding YOU agree!",
    "Payments & Settlements  **PLEASE READ**",
    "CONDITION FORMAT - PLEASE READ",
    "**RETURNS**",
    "Pickup **PLEASE READ**",
    "Shipping  **PLEASE READ**",
    "Payment Options, Credit Card Policy Update",
    "Pickup Process & Hours",
]

# Same words, real lots, most of them with live bids.
LOTS = [
    "Master Mystery Box - Please Read .........",
    "Mystery Box - Please Read .........",
    "Rubber Broom 12\" Head - Pet Hair Removal Carpets",
    "Dark Knight Trilogy DVD Lot Of 3 Batman Begins Dark Knight Returns",
    "How to train your dragon dvd",
    "How to Catch A Book Lot",
    "Klutz How to Build Pirate Ships Card Set",
    "Used Dell Precision 7750 Laptop (Qty. 1) FXA 92561",
    "Drieaz Humidifier ~ IA-25158",
    "$291.00 Remanufactured A/C Compressor",
]


def test_every_notice_is_recognised():
    for t in NOTICES:
        assert is_boilerplate(t), t


def test_no_real_lot_is_mistaken_for_one():
    for t in LOTS:
        assert not is_boilerplate(t), t


def test_one_product_word_is_enough_to_make_it_a_lot():
    """"Please Read" is the shared phrase; "Mystery Box" is what separates a
    $65 lot from a notice."""
    assert is_boilerplate("Pickup - Please Read")
    assert not is_boilerplate("Mystery Box - Please Read")


def test_filler_alone_is_not_a_notice():
    """A title needs a word the notice is ABOUT, or "All Items" would go."""
    for t in ("All Items", "Important", "Auction", "Monday", "Lot 1"):
        assert not is_boilerplate(t), t


def test_an_empty_title_is_not_a_notice():
    for t in ("", None, "   ", "12345", "***"):
        assert not is_boilerplate(t), t
