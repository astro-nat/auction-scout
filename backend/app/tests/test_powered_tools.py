"""Telling a motor from a motor-shaped word. Pure.

Every title here is a real one from the inventory. The user cannot test
whether a motor runs, so these lots are a risk they would rather not see -
but the words that name a motor also name blades, chains, attachments,
hand tools and, in two cases, a children's book series.
"""

import pytest

from app.services.powered import is_powered_tool

POWERED = [
    "Kobalt 10\" Table Saw w Foldable Stand / Dolly",     # a stand does not disqualify it
    "Heavy Duty Belt Sander On Rolling Stand",
    "Black & Decker Heavy-Duty Abrasive Chop Saw",
    "Honda GC190 Gas Powered Pressure Washer",
    "Clarke BT1033 Variable Speed Drill Press 5/8\"",
    "Porter Cable 6 Gal Portable Air Compressor",
    "Milwaukee HD 12\" Sliding Compound Miter Saw",
    "Ryobi ONE+ 18V Cordless Circular Saw w/ Battery",
    "Bosch 1582VS Double Insulated Jigsaw",
    "ECHO SRM-225 21.2cc Gas String Trimmer",
    "Ryobi BG612GSB Bench Grinder",
    "Porter Cable Plate Jointer Model 555",
    "Hobart P660 Commercial Floor Mixer - parts/repair",
    "Kubota EC808N Shop Vac 8 Gal Stainless Steel",
    "$239 1.5HP In/Above Ground Pool Pump 4750GPH Dual",
]

NOT_POWERED = [
    # the motor word names a part, not the machine
    "B&D Piranha Carbide-Tipped Circular Saw Blade",
    "New and Used Reciprocating Saw Blades",
    "Milwaukee Drill Press Accessory Stand",
    "Echo Blower Attachment For Weed Trimmer",
    "NEW PowerCare Pressure Washer Detergent Hose",
    "Mix Lot Garage Items, Level, Chainsaw Chains, Etc",
    # no motor at all
    "Vintage Metal Hand Crank Meat Grinder",
    "Vintage Two-Man Crosscut Logging Saw",
    "High-Pressure Bicycle Floor Pump",
    "Vintage Stanley 183 Builders Kit Router & Cutters",
    # a different sense of the word entirely
    "Fluke Networks Pro3000 Analog Tone Generator",
    "Clio BeautyTrim Personal Hair Trimmer Set",
    "Children's Book Lot - Disney, Amelia Bedelia, Jigsaw Jones",
    "Scholastic Children's Book Bundle - Dragon Slayers' Academy",
]


@pytest.mark.parametrize("title", POWERED)
def test_a_motor_the_buyer_cannot_test(title):
    assert is_powered_tool(title), title


@pytest.mark.parametrize("title", NOT_POWERED)
def test_not_a_motor(title):
    assert not is_powered_tool(title), title


def test_a_stand_does_not_make_it_an_accessory():
    """The first version guarded on "stand" and threw away two real saws
    that merely came with one."""
    assert is_powered_tool("Table Saw w Foldable Stand")
    assert not is_powered_tool("Drill Press Accessory Stand")


def test_hand_tools_are_left_alone():
    """They are testable by eye and they are on the BOLO list."""
    for t in ("Starrett 12 inch Combination Square",
              "Stanley Bailey No. 4 Hand Plane Type 11",
              "Estwing 16oz Claw Hammer"):
        assert not is_powered_tool(t), t


def test_nothing_is_read_from_an_empty_title():
    for t in ("", None, "   "):
        assert not is_powered_tool(t)
