"""Does a lot fit the mailer you ship in? Pure, apart from reading the size.

The user's call (2026-10-28): nothing gets shipped that can't go in their
14.5 x 20 in poly mailer - clothing, shoes and jackets included. The size
is a setting ("mailer_size", "14.5x20") they can change; a lot that won't
fit is never a gold mine.

Listings rarely give a full size, so the test goes from surest to roughest:
soft and flat goods always fit; a dimensions line is measured against the
mailer; a hard-to-ship lot doesn't; anything else fits if it weighs about
3 lb or less (a heavier parcel item doesn't go in a poly mailer).
"""

from __future__ import annotations

import re

from . import hibid

DEFAULT_SIZE = (14.5, 20.0)
SETTINGS_KEY = "mailer_size"
# Heaviest item, with no size to go on, that still goes in a mailer.
LIGHT_ENOUGH_LB = 3.0

# Soft or flat: folds, rolls or lies flat in a poly mailer whatever its
# printed size. Clothing and shoes by the user's say-so.
_ALWAYS_FITS_RE = re.compile(
    r"\b(?:shirts?|t-shirt|tee|jersey|blouse|tops?|sweaters?|cardigan|hoodies?|"
    r"sweatshirt|jackets?|coats?|vest|dress(?:es)?|gowns?|skirts?|jeans|pants|"
    r"shorts|leggings|pajamas|scarf|scarves|hat|cap|beanie|gloves|socks|"
    r"shoes|boots|sneakers|sandals|heels|loafers|slippers|"
    r"necklace|bracelet|earrings?|ring|brooch|pin|pinback|charm|pendant|"
    r"jewelry|watch|coins?|tokens?|medals?|patch(?:es)?|stamps?|postcards?|"
    r"(?:trading |baseball |football |sports )?cards?|comics?|magazines?|"
    r"\bcds?\b|\bdvds?\b|blu-?ray|vhs|vinyl|\blps?\b|record|"
    r"wallet|keychain|bag charm|purse|clutch|tote|scarf|tie|necktie|belt|"
    # Fabric bags fold flat, whatever size they open to.
    r"bags?|backpack|duffel)\b",
    re.IGNORECASE)
# Never in a mailer, even when a soft-goods word is in the title: a frame
# and glass don't fold, and a "record player", "belt sander", "hat rack" or
# "shoe organizer" isn't the clothing or vinyl its words suggest.
_NEVER_FITS_RE = re.compile(
    r"\b(?:framed|record player|turntable|jukebox|sander|"
    # A golf bag doesn't fold: a rigid tube with legs.
    r"golf (?:club |stand |cart )?bags?|"
    # A hitch, roof or bike rack names what a bag fits, not a rack.
    r"(?<!hitch )(?<!roof )(?<!bike )racks?|organizers?|"
    r"cabinet|shelf|shelves|stand)\b", re.IGNORECASE)


def parse_size(text: str | None) -> tuple[float, float] | None:
    """"14.5x20", "14.5 x 20", "20 by 14.5" -> (14.5, 20.0), narrow side first."""
    nums = re.findall(r"\d+(?:\.\d+)?", text or "")
    if len(nums) != 2:
        return None
    a, b = sorted(float(n) for n in nums)
    return (a, b) if 2 <= a <= 60 and 2 <= b <= 60 else None


def current_size() -> tuple[float, float]:
    from . import settings
    try:
        return parse_size(settings.get(SETTINGS_KEY)) or DEFAULT_SIZE
    except Exception:  # noqa: BLE001 - settings table missing on first boot
        return DEFAULT_SIZE


def size_label(size: tuple[float, float]) -> str:
    return f"{size[0]:g} x {size[1]:g} in"


def _fits_measured(sides: list[float], size: tuple[float, float]) -> bool:
    """A flat mailer bulges around what's in it: an item of length a, width b
    and thickness c needs a + c along the long side and b + c across."""
    a, b, c = (sorted(sides, reverse=True) + [1.0, 1.0])[:3]
    width, length = size
    return a + c <= length and b + c <= width


def fits(title: str | None, description: str | None, logistics_ease: str | None,
         weight_lb: float | None, size: tuple[float, float] | None = None) -> bool:
    size = size or current_size()
    t = title or ""
    if _NEVER_FITS_RE.search(t):
        return False
    if _ALWAYS_FITS_RE.search(t):
        return True
    sides = hibid.dimensions_inches(description)
    # "14" H x 6.5" Diameter": round, so the diameter is the width AND the
    # thickness - not a 1-inch-thick disc.
    if len(sides) == 2 and re.search(r"\bdiam", hibid.dimensions_line(description), re.I):
        sides.append(min(sides))
    if len(sides) >= 2:
        return _fits_measured(sides, size)
    if len(sides) == 1 and sides[0] > size[1]:
        return False                      # one side longer than the mailer
    if (logistics_ease or "").upper() == "HARD":
        return False
    return weight_lb is not None and float(weight_lb) <= LIGHT_ENOUGH_LB
