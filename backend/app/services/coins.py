"""Comp guards for coins and paper currency.

A keyword comp search is loose in exactly the ways that decide a coin's
price. The year, the mint mark, proof versus business strike, the grade on
the slab, one coin versus a set, an ounce versus a tenth: each moves the
value by multiples, and a search for "2012 proof silver eagle coin &
currency set" happily returns loose bullion eagles, other years and single
proofs. That lot drew 94 comps, priced at $188, and was flagged wrong.

What this module decides, all from text, all pure - comp_fits, one rule
per claim the LOT makes:

  - year: a comp naming a different year does not count
  - mint mark: "1921-S" is not priced off "1921-D"
  - proof: a proof lot needs proof comps; a lot that isn't one is not
    priced off proofs
  - grading: a raw coin is not priced off slabbed (PCGS/NGC/ANACS/ICG)
    comps, and a graded coin not off a different grade number
  - set vs single: a set (proof set, mint set, coin & currency set, roll,
    lot of N) only against sets, a single coin never against a set
  - bullion weight: 1 oz is not priced off 1/10 oz
  - note type: a silver certificate is not priced off Federal Reserve notes

Like funko.comp_fits, a comp that is silent on a point still counts -
requiring every comp to spell out its year and finish would empty the pool
- except proof, where a silent comp is usually the cheaper bullion strike.
"""

import re

# Words that only coins and paper money use. "Coin" alone is here, but the
# things that merely take coins are screened out below.
_COIN_RE = re.compile(
    r"\b(?:coins?|proof\s+set|mint\s+set|silver\s+eagle|gold\s+eagle|american\s+eagle|"
    r"morgan\s+(?:silver\s+)?dollars?|morgan\s+silver|peace\s+dollar|walking\s+liberty|mercury\s+dime|buffalo\s+nickel|"
    r"indian\s+head|wheat\s+(?:penny|pennies|cent)|half\s+dollars?|silver\s+dollars?|"
    r"kennedy\s+half|franklin\s+half|barber\s+(?:dime|quarter|half)|"
    # Circulating series, by name - "penny" or "quarter" alone would take in
    # penny loafers and quarter-zip pullovers.
    r"lincoln\s+(?:cents?|penny|pennies|memorial)|jefferson\s+nickels?|roosevelt\s+dimes?|"
    r"washington\s+quarters?|state\s+quarters?|eisenhower\s+dollars?|ike\s+dollars?|"
    r"sacagawea|susan\s+b\.?\s+anthony|standing\s+liberty|seated\s+liberty|"
    r"\d+\s*cents?\s+(?:coin|piece)|"
    r"bullion|troy\s*oz|numismatic|pcgs|ngc|anacs|"
    r"silver\s+certificate|gold\s+certificate|federal\s+reserve\s+note|"
    r"united\s+states\s+note|legal\s+tender\s+note|national\s+bank\s+note|"
    r"banknotes?|bank\s+notes?|star\s+note|red\s+seal|paper\s+money|currency)\b",
    re.IGNORECASE)
# Things that hold, count or take coins - not coins.
_NOT_COIN_RE = re.compile(
    r"\bcoin[\s-]*(?:purse|bank|op(?:erated)?|sorter|counter|holder|wrapper|tube|"
    r"album|folder|display|pusher|machine|cell|battery|mech|bag|pouch|sleeves?|wallet|"
    r"case|keychain|key\s+chain)s?\b|\bchallenge\s+coins?\b"
    r"|\bcoinbase\b|\bcurrency\s+(?:converter|counter|detector)\b",
    re.IGNORECASE)

_YEAR_RE = re.compile(r"\b(1[6-9]\d{2}|20[0-4]\d)\b")
# A mint mark rides on its year: "1921-S", "1921 S", "1878-CC".
_MINT_RE = re.compile(r"\b(1[6-9]\d{2}|20[0-4]\d)[\s-](P|D|S|O|CC|W)\b", re.IGNORECASE)
_PROOF_RE = re.compile(r"\b(?:proofs?|pr\s?-?\d{2}|pf\s?-?\d{2}|dcam|deep\s+cameo)\b",
                       re.IGNORECASE)
_GRADER_RE = re.compile(r"\b(?:pcgs|ngc|anacs|icg)\b", re.IGNORECASE)
_GRADE_RE = re.compile(r"\b(?:ms|pr|pf|sp)\s?-?(\d{2})\b", re.IGNORECASE)
_SET_RE = re.compile(
    r"\b(?:sets?|rolls?|lot\s+of|\d+\s*(?:pc|pcs|pieces?|coins))\b", re.IGNORECASE)
_CURRENCY_SET_RE = re.compile(r"\bcoin\s*(?:&|and)\s*currency\b", re.IGNORECASE)
_WEIGHT_RE = re.compile(r"(\d+\s*/\s*\d+|\d*\.\d+|\d+)\s*(?:troy\s*)?(?:oz|ounces?)\b",
                        re.IGNORECASE)
_NOTE_TYPES = {
    "silver certificate": re.compile(r"\bsilver\s+certificates?\b", re.IGNORECASE),
    "gold certificate": re.compile(r"\bgold\s+certificates?\b", re.IGNORECASE),
    "federal reserve note": re.compile(r"\bfederal\s+reserve\s+notes?\b|\bfrn\b", re.IGNORECASE),
    "united states note": re.compile(r"\b(?:united\s+states|legal\s+tender)\s+notes?\b|\bred\s+seal\b",
                                     re.IGNORECASE),
    "national bank note": re.compile(r"\bnational\s+bank\s+notes?\b", re.IGNORECASE),
}


# Trading cards that borrow a coin or banknote theme ("Gold Banknote Foil
# Card") are cards, priced as cards.
_CARD_RE = re.compile(r"\b(?:trading\s+cards?|foil\s+cards?|cards?\s+set|topps|panini|"
                      r"upper\s+deck|rookie\s+card)\b", re.IGNORECASE)


def is_coin(text: str) -> bool:
    """A coin, a coin set or paper money - not a coin purse or a coin op."""
    text = text or ""
    if _CARD_RE.search(text) or not _COIN_RE.search(text):
        return False
    # "Coin purse" alone is not a coin; "coin purse with 1964 silver half"
    # still is, because another coin word remains once it's removed.
    return bool(_COIN_RE.search(_NOT_COIN_RE.sub(" ", text)))


def _years(text):
    return set(_YEAR_RE.findall(text or ""))


def _mint_marks(text):
    return {(y, m.upper()) for y, m in _MINT_RE.findall(text or "")}


def _weights(text):
    out = set()
    for w in _WEIGHT_RE.findall(text or ""):
        w = w.replace(" ", "")
        try:
            if "/" in w:
                a, b = w.split("/")
                out.add(round(float(a) / float(b), 4))
            else:
                out.add(round(float(w), 4))
        except (ValueError, ZeroDivisionError):
            continue
    return out


def _note_types(text):
    return {name for name, rx in _NOTE_TYPES.items() if rx.search(text or "")}


def comp_fits(query: str, comp_title: str) -> bool:
    """False when a comp contradicts what this coin or note lot claims."""
    if not is_coin(query):
        return True
    comp = comp_title or ""

    lot_years, comp_years = _years(query), _years(comp)
    if lot_years and comp_years and not (lot_years & comp_years):
        return False

    comp_marks = _mint_marks(comp)
    for year, mark in _mint_marks(query):
        if any(y == year and m != mark for y, m in comp_marks):
            return False

    lot_proof, comp_proof = bool(_PROOF_RE.search(query)), bool(_PROOF_RE.search(comp))
    if lot_proof != comp_proof:
        return False

    if not _GRADER_RE.search(query) and _GRADER_RE.search(comp):
        return False
    lot_grades, comp_grades = set(_GRADE_RE.findall(query)), set(_GRADE_RE.findall(comp))
    if lot_grades and comp_grades and not (lot_grades & comp_grades):
        return False

    if bool(_SET_RE.search(query)) != bool(_SET_RE.search(comp)):
        return False
    if _CURRENCY_SET_RE.search(query) and not re.search(r"\bcurrency\b", comp, re.IGNORECASE):
        return False

    lot_w, comp_w = _weights(query), _weights(comp)
    if lot_w and comp_w and not (lot_w & comp_w):
        return False

    lot_notes, comp_notes = _note_types(query), _note_types(comp)
    if lot_notes and comp_notes and not (lot_notes & comp_notes):
        return False
    return True
