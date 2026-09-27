"""A fixed price so far below the item's own comps that it is bait.

The seller rule catches the account. This catches the listing, which is
the other half: a real person with real ratings can pass on a bulk lot
they were sold themselves, and a scammer with a warmed-up account looks
ordinary until you read the price.

The hard part is that "priced far below what it is worth" is what this
whole app looks for. A gold mine is exactly that. What separates a deal
from bait is not the ratio alone - the deepest discounts in the inventory
are $1 DVD bundles the comps value at $23, and those are simply cheap -
but the ratio together with the money at stake. A scam has to be worth
running, so it names something expensive and asks a fraction of it.

Both thresholds were read off the inventory, and the gap between deals and
bait is wide enough that the exact numbers barely matter: ratio under 25%
with a gap over $100 selects the same 12 listings as 30%/$100, 25%/$150 or
35%/$200. Those 12 are a $2,010 MacBook for $232, an RX 7900 XT for
$99.86, an Xbox Series X for $93, and nine more of the same shape.
"""

from typing import Optional

# Ask this far below the value, or lower, and it stops looking like a sale.
MAX_RATIO = 0.25

# ...but only when the claimed saving is worth a scammer's trouble. A $1
# DVD lot the comps say is worth $23 is not bait, it is a dollar.
MIN_GAP = 100.0


def implausible(ask: Optional[float], value: Optional[float]) -> bool:
    """True when this price is too far below this value to be a real sale."""
    if not ask or not value or ask <= 0 or value <= 0:
        return False
    return (value - ask) > MIN_GAP and (ask / value) < MAX_RATIO


def note(ask: Optional[float], value: Optional[float]) -> Optional[str]:
    """What to tell the user, or None when the price is ordinary."""
    if not implausible(ask, value):
        return None
    pct = round((ask / value) * 100)
    return (f"asking ${ask:,.0f} for something its own comps value at "
            f"${value:,.0f} - {pct}% of it. A real seller does not leave "
            f"${value - ask:,.0f} on the table")
