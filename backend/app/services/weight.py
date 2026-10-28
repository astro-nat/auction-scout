"""A lot's shipping weight in pounds, estimated from its own words. Pure.

Listings almost never print a weight (1 of #660's 505 did, and that was a
wheelchair's 350 lb capacity), and comps don't carry one. So this is a
rough figure from what the item is: the first matching item type wins,
times how many the title says there are; failing that, the ship tier.
An AI inspection replaces it with a weight read off the photos.
"""

from __future__ import annotations

import re

# (pattern on the lowercased title, pounds for ONE). Most specific first:
# "floor lamp" before "lamp", "golf bag" before "bag".
_ITEMS = [
    # Industrial light fixtures are cast housings, not lamps (#660 Pauluhn).
    (r"flood ?lights?|light fixture|high bay|explosion[- ]proof|hazardous location", 35),
    (r"wheelchair", 40), (r"commode", 20), (r"(?:weight|workout|exercise) bench", 45),
    (r"treadmill|elliptical", 150), (r"dresser|armoire|hutch|china cabinet", 120),
    (r"sofa|couch|loveseat|recliner", 100), (r"desk|table", 40), (r"chair", 20),
    (r"shelving unit|bookcase|bookshelf|cube (?:storage|organizer)|storage (?:shelf|organizer)", 20),
    (r"floor lamp|torchiere", 15), (r"walker|rollator", 8), (r"crutch", 6),
    (r"golf (?:club |stand |cart )?bag", 8), (r"golf clubs?", 4),
    (r"\b(?:tv|television)\b", 25), (r"amplifier|\bamp\b", 25),
    (r"record player|turntable|stereo|receiver|dvd player|vcr|radio", 10),
    (r"dutch oven", 13), (r"cast iron", 7), (r"skillet|frying pan", 4),
    # Umbrella before cooler: "Sun Country Cooler" is a brand on an umbrella.
    (r"umbrella", 5), (r"cooler", 8), (r"lantern", 4), (r"tent\b", 8),
    (r"landscape edging|edging", 12), (r"gutter", 3),
    (r"battery charger|jump starter", 8), (r"\bdrill|\bsaw\b|grinder|sander", 6),
    (r"hole saw|bit set|socket", 1.5),
    (r"guitar", 9), (r"drum", 3), (r"keyboard|synth", 15),
    # Framed means glass and a frame, whatever sits between the words:
    # "Framed Casablanca French Movie Poster".
    (r"framed\b.{0,40}?\b(?:poster|print|photo|art|picture|painting)", 8),
    (r"wall art|canvas", 5), (r"poster|print", 1),
    (r"mirror", 12), (r"\blamp\b", 6), (r"vase|pitcher|planter", 3),
    (r"plates?|platter|bowl|dish|baker|casserole", 3), (r"glass(?:es)?\b|mug|cup", 1),
    (r"fishing rods?", 2), (r"landing nets?", 2), (r"canes?\b|walking stick", 2),
    (r"helmet", 4), (r"baseball|football|basketball|soccer ball", 1),
    (r"jersey|shirt|t-shirt|hoodie|sweater|dress|jacket|coat|jeans|pants", 1.5),
    (r"gown", 4), (r"shoes|boots|sneakers", 3),
    (r"backpack|duffel", 2), (r"purse|handbag|crossbody|wallet|clutch|bag", 1.5),
    (r"books?\b", 1.5), (r"vinyl|record|\blps?\b", 0.6), (r"\bdvds?\b|\bcds?\b|blu-?ray|vhs", 0.4),
    (r"figurine|statue", 1.5), (r"swarovski|crystal", 0.5),
    (r"trading cards?|\bcards?\b", 0.3),
    (r"necklace|bracelet|earrings?|\bring\b|brooch|\bpin\b|pinback|charm|pendant", 0.2),
    (r"watch\b", 0.4), (r"coin|token|medal", 0.2),
]
_ITEMS_RE = [(re.compile(p), lb) for p, lb in _ITEMS]

# No item type matched: a parcel's worth by how hard it is to ship.
_BY_TIER = {"EASY": 1.0, "NEUTRAL": 5.0, "HARD": 40.0}

_COUNT_RES = [
    re.compile(r"\b(?:set|lot|pack|box) of (\d{1,2})\b"),
    re.compile(r"\((\d{1,2})\)"),
    re.compile(r"\b(\d{1,2})[- ]?(?:pc|pcs|piece|pack|pk)\b"),
]
_PAIR_RE = re.compile(r"\b(?:pair|duo|two|set of two)\b")
_MAX_COUNT = 12


def _count(title: str) -> int:
    for rx in _COUNT_RES:
        m = rx.search(title)
        if m:
            return max(1, min(int(m.group(1)), _MAX_COUNT))
    return 2 if _PAIR_RE.search(title) else 1


def estimate(title: str | None, logistics_ease: str | None = None) -> float | None:
    """Rough shipping weight in pounds, or None when there's nothing to go on."""
    t = (title or "").lower()
    for rx, lb in _ITEMS_RE:
        if rx.search(t):
            return round(lb * _count(t), 1)
    tier = (logistics_ease or "").upper()
    return _BY_TIER.get(tier)


def from_ai(value) -> float | None:
    """The AI's weight, when it's a plausible number of pounds."""
    try:
        lb = float(value)
    except (TypeError, ValueError):
        return None
    return round(lb, 1) if 0.05 <= lb <= 2000 else None
