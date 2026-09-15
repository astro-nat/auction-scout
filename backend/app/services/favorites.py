"""Auction houses the user wants surfaced first.

AuctionScout keeps its own list rather than mirroring HiBid's stars — reading
those would require the user's HiBid login, which this app deliberately never
handles. The company id is the same number either way, so starring a house on
HiBid and pasting its URL here lines the two up.
"""

import re
from typing import Optional

from sqlalchemy.orm import Session

from .. import models

# hibid.com/company/149798/budget-barn — also matches the bare id, and the
# /company/149798 form without the slug.
_COMPANY_URL_RE = re.compile(
    r"(?:hibid\.com/company/)?(\d{1,9})(?:/|$|\?)", re.IGNORECASE)


def parse_company_id(value: str | int) -> Optional[int]:
    """The HiBid company id out of a URL, a bare id, or a pasted link."""
    if isinstance(value, int):
        return value if value > 0 else None
    text = (value or "").strip()
    if not text:
        return None
    m = re.search(r"hibid\.com/company/(\d{1,9})", text, re.IGNORECASE)
    if m:
        return int(m.group(1))
    if text.isdigit():
        return int(text)
    return None


def ids(db: Session) -> set[int]:
    """Every favourited company id, for sorting and filtering."""
    return {row[0] for row in db.query(models.FavoriteAuctioneer.auctioneer_id).all()}


def add(db: Session, company_id: int, name: Optional[str] = None) -> models.FavoriteAuctioneer:
    row = (db.query(models.FavoriteAuctioneer)
             .filter(models.FavoriteAuctioneer.auctioneer_id == company_id).first())
    if row:
        if name and not row.name:
            row.name = name
            db.commit()
        return row
    # Fill the name from any auction already on file for this house, so the
    # list reads as names rather than bare numbers straight away.
    if not name:
        known = (db.query(models.Auction.auctioneer)
                   .filter(models.Auction.auctioneer_id == company_id)
                   .filter(models.Auction.auctioneer.isnot(None))
                   .first())
        name = known[0] if known else None
    row = models.FavoriteAuctioneer(auctioneer_id=company_id, name=name)
    db.add(row)
    db.commit()
    return row


def remove(db: Session, company_id: int) -> bool:
    deleted = (db.query(models.FavoriteAuctioneer)
                 .filter(models.FavoriteAuctioneer.auctioneer_id == company_id)
                 .delete(synchronize_session=False))
    db.commit()
    return bool(deleted)
