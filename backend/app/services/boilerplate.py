"""The auction house's own announcements, listed as if they were lots.

Every sale opens with a few of these - "**RETURNS**", "Payments &
Settlements **PLEASE READ**", "Pickup Process & Hours", "CLOSING TIME -
Friday, 6:30 PM CST". They are notices, not things to buy, and the app was
importing them, spending a sold-comps lookup on each and putting a value on
them: $56.70 for a returns policy, $43.11 for pickup hours. Then they had
to be hidden by hand.

Matching on the words alone would be wrong, and the inventory says so. The
same words appear in lots with live bids: "Master Mystery Box - Please Read
........." took $65, "Dark Knight Trilogy DVD Lot Of 3 ... Dark Knight
Returns" took $10, and "Pet Hair Removal" and "How to train your dragon
dvd" are ordinary listings.

So the test is not "does it mention returns" but "is it ENTIRELY
administrative": every word must be one a notice uses, and at least one
must be a word a notice is ABOUT. One product noun anywhere - mystery, box,
broom, dragon - and it is a lot.
"""

import re
from typing import Optional

# Words a notice is ABOUT. At least one has to be present, so that a short
# title made only of filler ("All Items") is not mistaken for one.
_SUBJECTS = {
    "returns", "return", "refund", "refunds",
    "payment", "payments", "paid", "settlement", "settlements", "invoice",
    "invoices", "checkout", "billing",
    "pickup", "pick", "removal", "removals", "loadout",
    "shipping", "shipment", "freight", "delivery",
    "terms", "policy", "policies", "disclaimer", "notice", "notices",
    "announcement", "announcements", "rules",
    "read", "bidding", "premium", "closing", "registration",
}

# Words a notice is allowed to CONTAIN. Anything outside these two sets is
# taken to be a product word, which makes the row a real lot.
_FILLER = {
    # instruction and courtesy
    "please", "before", "after", "by", "you", "your", "our", "we", "us",
    "agree", "agreement", "must", "will", "shall", "do", "does", "not",
    "no", "all", "any", "is", "are", "be", "this", "that", "these", "those",
    "and", "or", "the", "a", "an", "of", "to", "for", "with", "in", "on",
    "at", "from", "as", "if", "it", "its", "important", "attention",
    "carefully", "very", "first", "only", "every", "each", "more", "info",
    "information", "update", "updated", "new", "note", "notes", "how",
    # the vocabulary of a sale's own admin
    "auction", "auctions", "sale", "sales", "buyer", "buyers", "bidder",
    "bidders", "seller", "item", "items", "lot", "lots", "bid", "bids",
    "condition", "format", "options", "option", "method", "methods",
    "credit", "card", "cards", "cash", "check", "wire", "fee", "fees",
    "tax", "taxes", "accepted", "process", "procedure", "hours", "hour",
    "time", "times", "date", "dates", "day", "days", "week", "location",
    "address", "questions", "contact", "phone", "email", "office",
    # when
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday",
    "sunday", "am", "pm", "cst", "cdt", "est", "edt", "mst", "mdt", "pst",
    "pdt", "noon", "midnight",
}

_ALLOWED = _SUBJECTS | _FILLER


# Instructions that settle it on their own, whatever else the title says.
# "DO NOT BID ON THIS ITEM!" is a notice by any reading, and it turned up
# three times at the head of a reprice queue, about to spend a sold-comps
# lookup each on something nobody may buy.
#
# It needs its own rule because every word in it - do, not, bid, on, this,
# item - is filler, and the word test asks for something a notice is ABOUT.
# The alternative was to promote "bid" to a subject, which catches the same
# three lots on today's inventory and strictly more later: a bid card is a
# real thing an estate sale lists. An instruction not to bid is not.
_NOTICE_PHRASES = (
    "do not bid",
)


def is_boilerplate(title: Optional[str]) -> bool:
    """True when the title is one of the house's notices rather than a lot."""
    lowered = (title or "").lower()
    if any(p in lowered for p in _NOTICE_PHRASES):
        return True
    words = re.findall(r"[a-z]+", lowered)
    if not words:
        return False
    if not any(w in _SUBJECTS for w in words):
        return False
    return all(w in _ALLOWED for w in words)
