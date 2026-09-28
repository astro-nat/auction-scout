"""The never list, the inverse of BOLO. Pure.

Every title here is a real one from the inventory. The exceptions are the
point: each was a lot the first version of a rule wrongly swept up.
"""

import pytest

from app.services.never import SEEDS, label_for, rule_matches


class _Rule:
    def __init__(self, d):
        self.label = d["label"]
        self.phrases = d["phrases"]
        self.except_phrases = d.get("except_phrases")
        self.enabled = True


RULES = [_Rule(d) for d in SEEDS]

HIDDEN = [
    ("Chinese Style Cleaver Knife with Leather Sheath", "Sharp items"),
    ("Timber Wolf Zebra Stripe Folding Pocket Knife", "Sharp items"),
    ("12\" Estwing 24A Hatchet Axe", "Sharp items"),
    ("Fiskars Extendable Hedge Shears", "Sharp items"),
    ("NEW Avanti Pro Tooth Circular Saw Blade", "Sharp items"),
    ("Brutus 20\" Tile Cutter", "Sharp items"),
    ("Large 2 Tier 12 Alabaster Cylinders Chandelier",
     "Chandeliers and light fixtures"),
    ("Home Interiors Wooden Wall Sconce Set", "Chandeliers and light fixtures"),
    ("Globe Electric Edison Plug-In Pendant Light",
     "Chandeliers and light fixtures"),
    ("Shark Rotator LIFT-AWAY ADV Upright Vacuum", "Vacuum cleaners"),
    ("RIDGID WD06100 Wet/Dry Shop Vacuum", "Vacuum cleaners"),
    ("Hoover FloorMate SpinScrub Cleaner", "Vacuum cleaners"),
    ("Bissell QuickSteamer Small Carpet Steam Cleaner", "Vacuum cleaners"),
]

KEPT = [
    # every one of these was caught by a first draft and should not be
    "HD DVD lot of 6 includes Roy Orbison, King Kong, Twister, Blade Runner",
    "Zoe Sharp Audio Book CD Lot",
    "Vintage 1980s Cotton Fabric Remnants Lot Calico Floral Cutter Quilt",
    "Foldable Fan Blade LED Light Bulb",
    "X-ACTO Electric Pencil Sharpener",
    "Vintage Number 3 Handsaw Sharpening Vise Bench",
    "Johnson 54\" Dry Wall T Square Heavy Duty Blade",
    "LAKE CUBA, NY GLASS SOUVENIR HATCHET - SMALL CHIP",
    "Delta Heavy Duty Foldable Saw Stand",
    # ordinary lots with no edge, no fixture and no motor
    "Griswold No 8 Cast Iron Skillet",
    "Vintage Pyrex Atomic Starburst Casserole",
]


@pytest.mark.parametrize("title,label", HIDDEN)
def test_the_seeded_rules_cover_what_they_are_for(title, label):
    assert label_for(title, RULES) == label


@pytest.mark.parametrize("title", KEPT)
def test_nothing_else_is_swept_up(title):
    assert label_for(title, RULES) is None, title


def test_a_vacuum_sealer_is_not_a_vacuum_cleaner():
    """The exception that stops a kitchen gadget being hidden as a hoover."""
    assert label_for("FoodSaver Vacuum Sealer V4400", RULES) is None
    assert label_for("Lot of Vacuum Tubes for Amplifier", RULES) is None
    assert label_for("Shark Navigator Upright Vacuum", RULES) == "Vacuum cleaners"


def test_matching_is_whole_word():
    """"axe" must not match "faxed", "vac" must not match "vacate"."""
    assert not rule_matches("Faxed Documents Box", ["axe"], None)
    assert not rule_matches("Vacate Notice Forms", ["vac"], None)
    assert rule_matches("Camp Axe", ["axe"], None)


def test_a_disabled_rule_does_nothing():
    off = _Rule(SEEDS[0])
    off.enabled = False
    assert label_for("Folding Pocket Knife Collection", [off]) is None


def test_a_rule_with_no_phrases_matches_nothing():
    """An empty rule is what a half-finished edit looks like; it must not
    hide the whole inventory."""
    assert not rule_matches("Anything At All", [], None)
    assert not rule_matches("Anything At All", None, None)
    assert not rule_matches("Anything At All", ["", "  "], None)


def test_a_longer_phrase_wins_over_a_shorter_one():
    """Both are listed; the label is the same either way, but the exception
    lists rely on the longer phrase being tried first."""
    assert rule_matches("NEW Klutch 6-Piece HSS Saw Blade Set",
                        ["saw blade", "blade"], ["fan blade"])


def test_the_user_can_write_their_own():
    """The whole point: a new rule needs no code."""
    mine = _Rule({"label": "Exercise equipment",
                  "phrases": ["treadmill", "elliptical", "weight bench"],
                  "except_phrases": ["treadmill desk"]})
    assert label_for("NordicTrack Treadmill C700", [mine]) == "Exercise equipment"
    assert label_for("Standing Treadmill Desk", [mine]) is None
