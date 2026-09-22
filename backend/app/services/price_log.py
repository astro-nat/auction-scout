"""Every resale number the app has ever produced for a lot, kept.

The app shows one figure - the best-evidenced one. This keeps the rest,
including the ones that were wrong, because the wrong ones are the only
data that says where the algorithm fails.

The case that prompted it: a Swarovski members' gift worth about $25 came
back at $370 from twenty genuine sold comps, the audit rejected it and
suggested $80-150, and the comp filter now stops it happening at all. Each
of those three numbers is interesting, and before this only the last
survived - `est_resale` was overwritten in place and the earlier values
vanished with no way to tell how often, or how badly, comps overshoot.

Evidence tiers, strongest first. The UI shows the winner and says which
tier it came from; everything else is here.
"""

import logging
from typing import Optional

from .. import models
from ..database import SessionLocal

logger = logging.getLogger(__name__)

# Higher wins. Used to pick what the app displays and to judge, later,
# whether a method is worth its cost.
EVIDENCE_RANK = {
    "sold": 100,        # real completed sales, three or more agreeing
    "retail": 90,       # the price printed on the lot title by the house
    "audit": 70,        # a second opinion that replaced a rejected value
    "sold_thin": 60,    # completed sales, but only one or two
    "itemized": 50,     # the vision pass summing what it can see
    "active": 30,       # asking prices, discounted - hope, not evidence
    "ai": 20,           # the model's own guess
    "house": 10,        # the auctioneer's estimate, which is promotional
}


def rank(evidence: Optional[str]) -> int:
    return EVIDENCE_RANK.get(evidence or "", 0)


def classify(price_source: Optional[str], comp_count: Optional[int]) -> str:
    """Map a price_source string onto an evidence tier.

    Derived from the string rather than stored separately because every
    existing row already carries it, so old data classifies too.
    """
    src = (price_source or "").lower()
    n = comp_count or 0
    if src.startswith("retail $"):
        return "retail"
    if src.startswith("audit-corrected"):
        return "audit"
    if "itemized" in src:
        return "itemized"
    if "sold" in src and "active" not in src:
        return "sold" if n >= 3 else "sold_thin"
    if "active" in src:
        return "active"
    if "ai estimate" in src or src.startswith("ai"):
        return "ai"
    if "house" in src:
        return "house"
    return ""


def record(lot_id: int, value, *, method: str, price_source: str = None,
           comp_count: int = None, chosen: bool = True,
           rejected: bool = False, note: str = None,
           query: str = None) -> None:
    """Write one observation. Never raises - losing a log row must not cost
    the work that produced it."""
    db = None
    try:
        db = SessionLocal()
        db.add(models.PriceObservation(
            lot_id=lot_id,
            value=value,
            method=method,
            price_source=(price_source or "")[:200] or None,
            comp_count=comp_count,
            evidence=classify(price_source, comp_count) or method,
            chosen=chosen,
            rejected=rejected,
            note=(note or "")[:400] or None,
            query=(query or "")[:200] or None,
        ))
        db.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Price observation for lot %s skipped: %s", lot_id, exc)
    finally:
        if db is not None:
            db.close()
