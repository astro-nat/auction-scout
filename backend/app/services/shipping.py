"""Does this auction house ship into the United States?

Only a question for houses outside the US. A HiBid "Anywhere" scan surfaces
Canadian auctions looking like any other, and a good share of them ship
within Canada only - a lot won there can never arrive. `state` carries the
province code, which is how a house is known to be Canadian; the shipping
and terms text says whether the border gets crossed. The rules here catch
the plainly worded cases for free at import time; the AI read
(workers.enrich.SHIPPING_PROMPT) is asked the same question for the rest.

None means "not known", never "no". A US house is always None: it has no
border to cross, and its pickup-only status is a different flag.
"""

import re
from typing import Optional

CANADIAN_PROVINCES = frozenset({
    "AB", "BC", "MB", "NB", "NL", "NS", "NT", "NU", "ON", "PE", "QC", "SK", "YT",
})


def is_canadian(state: Optional[str]) -> bool:
    """HiBid writes the province as it likes ("ON", "On", "on ")."""
    return (state or "").strip().upper() in CANADIAN_PROVINCES


# "US" is also a pronoun ("contact us", "shipped back to us"), so the
# abbreviation is only recognised in capitals; the spelled-out forms in any
# case. Every mention is folded to one token the rules below can name.
_US_ABBREV_RE = re.compile(r"\bU\.?S\.?(?:A\.?)?(?![A-Za-z])")
_US_WORDS_RE = re.compile(r"\bunited\s+states(?:\s+of\s+america)?\b|\busa\b", re.IGNORECASE)
_US = r"(?:the\s+)?unitedstates"

_NO_RULES = [re.compile(p, re.IGNORECASE) for p in (
    # "Canada only", "within Canada only", "Canadian addresses only"
    r"\b(?:canada|canadian\s+(?:addresses|residents|buyers|bidders|customers))\s+only\b",
    # "we ship within Canada", "shipping in Canada"
    r"\bship\w*\s+(?:only\s+)?(?:is\s+)?(?:available\s+)?(?:with)?in\s+canada\b",
    r"\bonly\s+ship\w*\s+(?:with)?in\s+canada\b",
    # "do not ship to the US", "cannot ship outside Canada", "will not ship internationally"
    r"\b(?:do(?:es)?\s+not|don'?t|no|not|cannot|can'?t|unable\s+to|will\s+not|won'?t)\s+"
    r"(?:be\s+)?(?:able\s+to\s+)?ship\w*\s+(?:items\s+|lots\s+|anything\s+|purchases\s+)?"
    r"(?:to\s+|outside\s+(?:of\s+)?)?(?:" + _US + r"|international\w*|outside\s+(?:of\s+)?canada)",
    r"\bno\s+(?:international|cross[\s-]?border|" + _US + r")\s+ship\w*",
    r"\bdomestic\s+(?:ship\w*\s+)?only\b",
)]

_YES_RULES = [re.compile(p, re.IGNORECASE) for p in (
    # "ship to the US", "we can ship into the United States"
    r"\bship\w*[^.;\n]{0,60}?\b(?:to|into|across)\s+(?:canada\s+(?:and|&|or)\s+)?" + _US + r"\b",
    r"\b" + _US + r"\s+(?:and\s+canad\w+\s+)?(?:customers|buyers|bidders|residents|addresses)\b",
    r"\binternational\s+ship\w*\s+(?:is\s+)?(?:available|offered|welcome|possible)",
    r"\bship\w*\s+(?:worldwide|internationally|international)\b",
    r"\b(?:canada\s+(?:and|&|or)\s+" + _US + r"|" + _US + r"\s+(?:and|&|or)\s+canada)\b",
)]


def _normalize(text: str) -> str:
    text = _US_ABBREV_RE.sub(" unitedstates ", text or "")
    return _US_WORDS_RE.sub(" unitedstates ", text)


def ships_to_us_from_text(ship_text: str, terms_text: str) -> Optional[bool]:
    """What the posted text plainly says. A text that both promises and
    refuses ("we ship to the US" ... "no international shipping") is left
    to the AI read rather than guessed at."""
    hay = _normalize(f"{ship_text or ''}\n{terms_text or ''}")
    if not hay.strip():
        return None
    # Refusals are cut out before the offers are looked for: "we do not
    # ship to the US" contains "ship to the US", and must not count as one.
    no = False
    for r in _NO_RULES:
        hay, n = r.subn(" ", hay)
        no = no or n > 0
    yes = any(r.search(hay) for r in _YES_RULES)
    if no and not yes:
        return False
    if yes and not no:
        return True
    return None


def resolve(state: Optional[str], ship_text: str, terms_text: str,
            ai: Optional[dict]) -> Optional[bool]:
    """The stored answer for one auction: the text's plain statement first,
    then the AI's reading, then its pickup-only verdict (a house that ships
    nothing ships nothing across the border either)."""
    if not is_canadian(state):
        return None
    verdict = ships_to_us_from_text(ship_text, terms_text)
    if verdict is not None:
        return verdict
    if ai:
        v = ai.get("ships_to_us")
        if isinstance(v, bool):
            return v
        if ai.get("ships") is False:
            return False
    return None
