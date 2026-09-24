"""Fraud and mispricing guards for Funko Pops.

Funko is where a keyword comp search is easiest to fool. Two signed pops on
2026-09-24 were valued at $75 and $80 off comps that were almost all
authenticated autographs - JSA and Beckett stickers, some of them of other
people entirely (Undertaker and Larry Bird comping a Pedro Martinez #55) -
while neither listing named any authenticator. An unauthenticated signature
at a general auction is worth the pop underneath it until proven otherwise,
and the pop underneath is a $10-20 item.

What this module decides, all from text, all pure:

  - signed without a named authenticator: search without the signature
    words, so the value is the unsigned pop's
  - signed WITH one named: price the signed comps, but mark the lot
    "authenticate first" - COA stickers are faked too, and the cert number
    is checkable online before bidding
  - custom / bootleg / knockoff / fan-made: never a gold mine
  - loose / out of box: priced against loose pops, not boxed ones
  - comps that do not fit the lot (comp_fits): an unsigned or plain pop is
    not priced off signed, chase, glow, flocked or convention variants, and
    a lot with a box number is not priced off a different box number

The AI inventing a variant the listing never claimed ("Chase", "Signed")
is caught with the other invented identifiers in pricing.invented_identifiers,
through variant_claims below.
"""

import re
from collections import Counter
from typing import Optional

_FUNKO_RE = re.compile(r"\bfunko\b|\bpop!", re.IGNORECASE)

# "Signature" is left out on purpose: it is also the name of product lines.
_SIGNED_RE = re.compile(
    r"\b(?:signed|autographed|autograph|auto'?d|inscribed)\b"
    r"|\bauto\b(?!\s*(?:parts?|mobile|motive|matic|repair))",
    re.IGNORECASE)

# Authenticators whose certs can be looked up. A name here is not proof -
# fake COA stickers exist - but it is something to verify before bidding.
_AUTHENTICATOR_RE = re.compile(
    r"\b(?:jsa|james\s+spence|beckett|bas|psa(?:\s*/?\s*dna)?|fanatics|steiner|"
    r"tristar|swau|upper\s+deck\s+authenticated|uda|mlb\s+authenticated|"
    r"galaxy\s*con|fan\s+expo\s+coa)\b",
    re.IGNORECASE)

_BOOTLEG_RE = re.compile(
    r"\b(?:custom(?:ized)?|bootleg|knock[\s-]?off|replica|counterfeit|fake|"
    r"fan[\s-]?made|3d[\s-]?printed|unofficial|unlicensed|not\s+(?:a\s+)?"
    r"(?:genuine|authentic|official|real)(?:\s+funko)?)\b",
    re.IGNORECASE)

_LOOSE_RE = re.compile(
    r"\b(?:loose|oob|unboxed|out\s+of\s+(?:the\s+)?box|no\s+box|without\s+(?:the\s+)?box|"
    r"missing\s+(?:the\s+)?box|box\s+missing)\b",
    re.IGNORECASE)

# Variants that multiply a pop's value. A plain pop priced off these is
# inflated; a lot that claims one it doesn't have is misidentified.
_VARIANT_RE = re.compile(
    r"\b(?:chase|glow(?:\s+in\s+the\s+dark)?|gitd|flocked|metallic|diamond(?:\s+collection)?|"
    r"chrome|blacklight|black\s+light|sdcc|nycc|eccc|wondercon|convention|"
    r"limited\s+edition|le\s*\d{2,6}|1\s+of\s+\d{2,6}|signed|autographed|autograph)\b",
    re.IGNORECASE)

_BOX_NUMBER_RE = re.compile(r"#\s*(\d{1,5})\b")


def _canon(variant: str) -> str:
    """One name per variant, so "Autographed" in a comp matches "Signed" in
    the lot and "GITD" matches "Glow in the Dark"."""
    v = re.sub(r"\s+", " ", variant.lower())
    if v in ("signed", "autographed", "autograph"):
        return "signed"
    if v.startswith("glow") or v == "gitd":
        return "glow"
    if v in ("blacklight", "black light"):
        return "blacklight"
    if v.startswith("diamond"):
        return "diamond"
    if v.startswith(("le", "1 of", "limited")):
        return "limited"
    return v


def _variants(text: str) -> Counter:
    """Variant mentions, COUNTED. "Chase" is a variant and also a name -
    Chevy Chase, Paw Patrol's Chase - so a comp only claims the variant
    when it says "chase" more times than the lot does."""
    return Counter(_canon(m.group(0)) for m in _VARIANT_RE.finditer(text or ""))


def is_funko(text: str) -> bool:
    return bool(_FUNKO_RE.search(text or ""))


def assess(listing_text: str) -> Optional[dict]:
    """What the listing itself says about a Funko lot. None when the lot is
    not a Funko at all."""
    text = listing_text or ""
    if not is_funko(text):
        return None
    signed = bool(_SIGNED_RE.search(text))
    auth = _AUTHENTICATOR_RE.search(text) if signed else None
    bootleg = _BOOTLEG_RE.search(text)
    loose = bool(_LOOSE_RE.search(text))
    notes = []
    if bootleg:
        notes.append(f"listing says \"{bootleg.group(0)}\" - not a genuine Funko")
    if signed and not auth:
        notes.append("signature not authenticated (no JSA / Beckett / PSA named) - "
                     "priced as the unsigned pop")
    if auth:
        notes.append(f"signed, {auth.group(0)} named - verify the cert number before bidding")
    if loose:
        notes.append("loose / out of box - priced against loose pops")
    return {
        "signed_unverified": signed and not auth,
        "authenticator": auth.group(0) if auth else None,
        "bootleg": bootleg.group(0) if bootleg else None,
        "loose": loose,
        "block": bool(bootleg),
        "note": "; ".join(notes)[:300] or None,
    }


def search_query(query: str, a: Optional[dict]) -> str:
    """The comp search for a Funko lot: signature words out when nobody
    vouches for the signature, "loose" in when the box is gone."""
    if not a or not query:
        return query
    q = query
    if a["signed_unverified"]:
        q = _SIGNED_RE.sub(" ", q)
    if a["loose"] and not _LOOSE_RE.search(q):
        q = f"{q} loose"
    return re.sub(r"\s+", " ", q).strip()


def comp_fits(query: str, comp_title: str) -> bool:
    """False when a comp would inflate this Funko lot's value.

    One-directional, like pricing._promo_match: the lot's own claims decide
    what may comp it. A plain pop must not be priced off a variant or a
    signed one; a lot with a box number must not be priced off a different
    number. Comps that are silent on a point still count - requiring them
    to speak would empty the pool.
    """
    if not is_funko(query):
        return True
    comp = comp_title or ""
    if _variants(comp) - _variants(query):      # Counter subtraction keeps only excess
        return False
    lot_nums = set(_BOX_NUMBER_RE.findall(query))
    comp_nums = set(_BOX_NUMBER_RE.findall(comp))
    if lot_nums and comp_nums and not (lot_nums & comp_nums):
        return False
    return True


def variant_claims(listing_text: str, ai_title: str) -> list[str]:
    """Variants the AI title claims for a Funko that the listing never did."""
    if not ai_title or not (is_funko(listing_text) or is_funko(ai_title)):
        return []
    extra = _variants(ai_title) - _variants(listing_text)
    out = []
    for m in _VARIANT_RE.finditer(ai_title):
        word = m.group(0)
        if extra[_canon(word)] > 0 and word not in out:
            out.append(word)
            extra[_canon(word)] -= 1
    return out
