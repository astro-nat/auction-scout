"""What a bulk media lot costs PER ITEM.

Asked for as: "find lots on vinted where the dvd/cd/other media lots average
to less than 40 cents/item".

A pile of discs is only worth buying by the piece, and the ask price alone
cannot tell you that - "$16" is a steal for 40 CDs and a waste for 2. The
count is almost always in the seller's own title, so this reads it there and
divides. Nothing here calls out to anything; it is title arithmetic.

Everything is deliberately conservative: a title that does not clearly state
a quantity of media returns None rather than a guess, because a wrong count
does not produce a slightly wrong number, it produces a fake bargain at the
top of a sorted list.
"""

import re
from typing import Optional

# What counts as media worth buying in bulk to resell.
_MEDIA_RE = re.compile(
    r"\b(cds?|dvds?|blu-?rays?|vhs|lps?|records?|vinyl records?|cassettes?|"
    r"books?|novels?|comics?|magazines?|movies?)\b", re.I)

# Words that mean the lot is about media WITHOUT being media. "Vinyl" is a
# sticker material far more often than a record, and an empty case, a sleeve
# or a disc rack is packaging. All of these were real matches on real
# listings: "80 pcs holographic stickers", "Lot of 46 Clear Vinyl Coin
# Sleeves", "Lot of 10 empty dvd cases", "Lot of 3 Book Sox book covers".
_NOT_MEDIA_RE = re.compile(
    r"\b(stickers?|decals?|sleeves?|coin|empty|covers?|case only|cases only|"
    r"players?|shelf|shelves|rack|racks|storage|binder|binders)\b", re.I)

# The unit a number has to be counting for it to be a quantity of media.
_UNIT = (r"(?:cds?|dvds?|blu-?rays?|vhs|lps?|records?|books?|novels?|"
         r"cassettes?|discs?|movies?|albums|comics?|magazines?)")

# Tried in order. The "lot of N" forms come first because they are explicit:
# there, even a number that looks like a year is a count.
_EXPLICIT = (
    re.compile(r"\blot of (\d{1,4})\b", re.I),
    re.compile(r"\bbundle of (\d{1,4})\b", re.I),
    re.compile(r"\bset of (\d{1,4})\b", re.I),
)
_IMPLICIT = (
    re.compile(rf"\b(\d{{1,4}})\s*(?:x\s*)?(?:mixed\s+|assorted\s+|random\s+)?{_UNIT}\b", re.I),
    re.compile(r"\b(\d{1,4})\s*(?:pcs?|pieces?|count|ct)\b", re.I),
)

# A bare 1900-2099 in a title is a date - a pressing year, an edition, a film
# release. "Billboard Top Hits 1989 CD" is not 1,989 CDs, and read as one it
# sorts to the very top of a cheapest-per-item list at a fifth of a cent.
_YEAR_RE = re.compile(r"^(?:19|20)\d{2}$")

# Below 2 it is not a lot; above this a title is describing something else
# (a catalogue number, a model, a year that slipped through).
MIN_COUNT = 2
MAX_COUNT = 500


def is_media_lot(title: Optional[str]) -> bool:
    """Does this title describe media, rather than something merely near it?"""
    t = title or ""
    return bool(_MEDIA_RE.search(t)) and not _NOT_MEDIA_RE.search(t)


def item_count(title: Optional[str]) -> Optional[int]:
    """How many pieces the seller says are in the lot, or None.

    None is the common answer and the right one: "Huge DVD lot" states no
    quantity, and inventing one would put a made-up price per item on it.
    """
    t = title or ""
    for pattern in _EXPLICIT:
        m = pattern.search(t)
        if m:
            n = int(m.group(1))
            if MIN_COUNT <= n <= MAX_COUNT:
                return n
    for pattern in _IMPLICIT:
        for m in pattern.finditer(t):
            raw = m.group(1)
            if _YEAR_RE.match(raw):
                continue        # a date, not a quantity
            n = int(raw)
            if MIN_COUNT <= n <= MAX_COUNT:
                return n
    return None


def per_item(price, title: Optional[str]) -> Optional[float]:
    """Ask price divided by the stated piece count, or None when either is
    missing. Rounded to the tenth of a cent - the interesting range is a few
    cents wide."""
    if not is_media_lot(title):
        return None
    n = item_count(title)
    if not n:
        return None
    try:
        p = float(price)
    except (TypeError, ValueError):
        return None
    if p <= 0:
        return None
    return round(p / n, 3)
