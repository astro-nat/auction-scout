"""Same title, one pricing.

Liquidation houses list the same product many times over - twelve "WZU
Recordable Personal Safety Alarm" lots in one sale - and each was being
identified and priced on its own: twelve AI calls, the same sold-comps
search sent three and four times, and twelve slightly different values for
one product. Lots whose listing titles match are the same item; the first
one priced sets the value for all of them.

A title only groups when it names a product. "Pyrex" or "Pyrex dish" name a
category - nine "Pyrex" lots in one sale were nine different dishes from $15
to $70 - so a title needs at least MIN_WORDS meaningful words to group.
"""

import re
from typing import Optional

from sqlalchemy import func

from .. import models

MIN_WORDS = 3

# Too common to tell two listings apart.
_STOPWORDS = {"the", "and", "for", "with", "of", "to", "in", "on", "a", "an",
              "by", "or", "new", "used", "set", "size", "lot", "item", "items"}


def title_key(title: Optional[str]) -> Optional[str]:
    """The grouping key: the title's letters and digits, lower-cased, runs of
    anything else collapsed to one space. None when the title is too
    generic to group. key_sql() computes the same string in Postgres."""
    words = re.findall(r"[a-z0-9]+", (title or "").lower())
    meaningful = [w for w in words if len(w) >= 3 and w not in _STOPWORDS]
    if len(meaningful) < MIN_WORDS:
        return None
    return " ".join(words)


def key_sql(column=models.Lot.title):
    """title_key in SQL, for matching rows without loading them."""
    return func.trim(func.regexp_replace(func.lower(column), "[^a-z0-9]+", " ", "g"))

# --- grouping a product across its individual units -----------------------
#
# A liquidation house lists one product many times and tells the copies
# apart with an asset tag: "Used Dell Precision 7750 Laptop (Qty. 1) FXA
# 92561" and "... FXA 92608" are the same laptop, and "Drieaz Humidifier ~
# IA-25158" has two siblings. title_key keeps those apart, because for
# PRICING they are separate lots that happen to be alike. For hiding they
# are one decision.
#
# The cut is deliberately timid. It drops what follows the last real word
# in the title, and only when that tail carries a number of four digits or
# more - a serial or asset tag. Without that guard "iPhone 13 Pro Max"
# would lose its "13" and group with every other iPhone, and a mid-title
# model number is never touched: 7750 and 7760 stay different laptops.

# The unit identifier is almost always fenced off by punctuation, because
# a human has to read past it too: "HARD DRIVE REMOVED ~ LAPTOP ~ FB-9-120
# (R17D)", "Drieaz Humidifier ~ IA-25158", "Dell Precision 7750 Laptop
# (Qty. 1) FXA 92561". Cutting at the fence is what groups those three
# laptops, which a digit-counting rule missed - FB-9-120 has no run of
# four digits anywhere in it.
#
# The segment is only dropped when it CONTAINS a digit, which is what
# keeps a variant: "(Qty. 1)" and "(R17D)" go, "(Large)" stays, because a
# large bowl is not the same product as a small one.
_FENCE_RE = re.compile(r"(?:\s~|\s\(|\s#|\s\*)")
_HAS_DIGIT_RE = re.compile(r"\d")

# Four or more digits in a row: a serial or asset tag with no fence around
# it, as in "... Laptop FXA 92561".
_SERIAL_RE = re.compile(r"\d{4,}")

# Four or more letters in a row: a word naming the thing, not a code.
_REAL_WORD_RE = re.compile(r"[a-z]{4,}")

# Two meaningful words is enough to group for hiding, where MIN_WORDS is
# three. The thresholds differ because the mistakes differ: a wrong pricing
# group silently puts one lot's value on another, while a wrong hide group
# is shown as a count, confirmed before anything happens, and undone by
# unhiding. "Drieaz Humidifier" has only two words and three copies in the
# sale, and refusing to group it is the worse error here.
HIDE_MIN_WORDS = 2


def _strip_unit_id(title: str) -> str:
    """Drop the fenced-off identifier segments from the end of a title."""
    out = title
    while True:
        cuts = [m.start() for m in _FENCE_RE.finditer(out)]
        if not cuts:
            return out
        tail = out[cuts[-1]:]
        if not _HAS_DIGIT_RE.search(tail):
            return out
        out = out[:cuts[-1]]


def product_key(title: Optional[str]) -> Optional[str]:
    """The grouping key for "everything like this one", or None when the
    title is too generic to group on."""
    words = re.findall(r"[a-z0-9]+", _strip_unit_id(title or "").lower())
    if not words:
        return None
    # No fence, but a bare serial still trails a real word.
    last_word_at = None
    for i, w in enumerate(words):
        if _REAL_WORD_RE.search(w):
            last_word_at = i
    if last_word_at is not None and last_word_at < len(words) - 1:
        tail = words[last_word_at + 1:]
        if any(_SERIAL_RE.search(t) for t in tail):
            words = words[:last_word_at + 1]
    meaningful = [w for w in words if len(w) >= 3 and w not in _STOPWORDS]
    if len(meaningful) < HIDE_MIN_WORDS:
        return None
    return " ".join(words)
