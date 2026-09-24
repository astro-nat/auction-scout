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
