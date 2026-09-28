"""The never list: kinds of thing the user does not buy.

The inverse of BOLO. BOLO says "flag this, it is worth money to me"; this
says "hide this, I do not want it". It is a list rather than code for the
same reason BOLO is: the user thinks of a new one every few minutes, and
none of them should need a deploy.

Matching is whole-word, so "axe" does not match "faxed" and "vac" does not
match "vacate". Each rule carries its own exceptions, which is the part a
plain keyword list gets wrong: every word that names a thing also names
something else. "Blade" is Blade Runner. "Jigsaw" is a children's book
series. "Razor" is a scooter. A glass souvenir hatchet has no edge on it.
"""

import re
from typing import Iterable, Optional

# Shipped with the app, already checked against the whole inventory. The
# user can edit or delete any of them; `seeded` only decides what a reseed
# is allowed to replace.
SEEDS = [
    {
        "label": "Sharp items",
        "phrases": [
            "knife", "knives", "machete", "dagger", "cleaver",
            "axe", "axes", "hatchet", "sword", "katana",
            "razor", "scalpel", "scissors", "shears", "snips",
            "blade", "blades", "saw blade", "saw blades",
            "box cutter", "tile cutter", "pipe cutter", "glass cutter",
            "bolt cutter", "cutter",
        ],
        "except_phrases": [
            # makes an edge rather than having one
            "sharpener", "sharpening", "grinding", "hone", "honing", "strop",
            # a title, an author or a character
            "dvd", "blu-ray", "bluray", "cd", "vhs", "book", "books",
            "audio", "movie", "film", "novel", "runner",
            # the edge belongs to something harmless
            "fan blade", "fan blades", "ceiling fan", "wiper", "propeller",
            # a cutter quilt is a quilt; a cookie cutter is a biscuit
            "quilt", "cookie", "biscuit", "fondant", "pastry",
            # the holder, not the thing
            "stand", "block", "storage", "rack",
            # a Razor is also a scooter; a T-square's blade is a ruler
            "scooter", "square",
            # an ornament shaped like a hatchet
            "souvenir", "ornament", "replica", "toy",
        ],
    },
    {
        "label": "Chandeliers and light fixtures",
        "phrases": ["chandelier", "chandeliers", "sconce", "sconces",
                    "pendant light", "pendant lights"],
        "except_phrases": ["bulb only", "shade only"],
    },
    {
        "label": "Vacuum cleaners",
        "phrases": ["vacuum", "vacuums", "shop vac", "shop vacuum",
                    "wet/dry vac", "wet dry vac", "hoover", "dyson", "roomba",
                    "carpet cleaner", "steam cleaner", "floormate"],
        # A vacuum SEALER is a kitchen gadget, and a vacuum TUBE is an
        # amplifier part - neither is a vacuum cleaner.
        "except_phrases": ["vacuum sealer", "vacuum tube", "vacuum tubes",
                           "vacuum flask", "vacuum bag", "vacuum bags"],
    },
]


def _pattern(phrases: Iterable[str]) -> Optional[re.Pattern]:
    """One whole-word alternation over the phrases, or None for an empty list."""
    parts = [re.escape(p.strip()) for p in (phrases or []) if p and p.strip()]
    if not parts:
        return None
    # Longest first so "saw blade" wins over "blade" when both are listed.
    parts.sort(key=len, reverse=True)
    return re.compile(r"\b(?:" + "|".join(parts) + r")\b", re.I)


def rule_matches(title: Optional[str], phrases, except_phrases) -> bool:
    """Does this one rule cover this title?"""
    t = title or ""
    hit = _pattern(phrases)
    if hit is None or not hit.search(t):
        return False
    skip = _pattern(except_phrases)
    return not (skip is not None and skip.search(t))


def label_for(title: Optional[str], rules) -> Optional[str]:
    """The label of the first enabled rule covering this title, or None.

    Rules are tried in the order given, so the caller decides precedence;
    the label is what the UI shows as the reason, which matters more than
    which of two overlapping rules won.
    """
    for r in rules:
        if getattr(r, "enabled", True) and rule_matches(
                title, getattr(r, "phrases", None), getattr(r, "except_phrases", None)):
            return getattr(r, "label", None)
    return None
