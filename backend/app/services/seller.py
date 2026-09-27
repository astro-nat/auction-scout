"""How much a Vinted seller's account backs up their listing.

An auction house is the seller on every other source, and a house has a
history. Vinted does not: anyone can list anything an hour after signing
up, and the cheapest way to run a scam is a fresh account with one
underpriced thing on it. Those listings price beautifully - a $450 pair of
SSDs asking $65 is a gold mine by arithmetic - which is exactly backwards.

The shape comes from the inventory. Of the 13 Vinted sellers behind a gold
badge, 9 had no feedback at all, one or two listings, and had never bought
anything. None of 14 sellers drawn at random looked like that: the ones
with no feedback still had four to twenty-seven listings, or had bought
things themselves.

So it is the COMBINATION that means something, and each part alone is
innocent. A new seller has to start somewhere; a small closet is just a
small closet; buying nothing is ordinary for someone clearing a wardrobe.
Together they describe an account that exists only to hold this listing.
"""

from typing import Optional


def untrusted(feedback_count: Optional[int],
              item_count: Optional[int],
              bought_count: Optional[int]) -> bool:
    """True when the account has no history behind the listing at all."""
    if item_count is None:
        # Never looked up, or the lookup failed. Unknown is not guilty:
        # every non-Vinted lot in the database is in this state, and so is
        # any seller Vinted would not answer for.
        return False
    if feedback_count:                       # any rating at all is a history
        return False
    if item_count > 2:                       # a real closet, unrated or not
        return False
    return not (bought_count or 0)           # having bought is still history


def note(rating: Optional[float], feedback_count: Optional[int],
         item_count: Optional[int], bought_count: Optional[int]) -> Optional[str]:
    """What to tell the user, or None when there is nothing to say."""
    if not untrusted(feedback_count, item_count, bought_count):
        return None
    n = item_count or 0
    return (f"seller has no ratings, {n} listing{'' if n == 1 else 's'} "
            "and has never bought anything")
