"""When an auction is actually over.

An auction's closing_date is when its lots START closing. HiBid closes them
one after another: PART 2 - Watermark Estate's first lot closed at 6:02 pm
and the rest ran on for hours after. Every "is this auction closed" check
that read closing_date alone treated the whole sale as over at 6 pm - 853
open lots shown as closed, their bids no longer refreshed, their pricing
skipped, and the automatic flush set to delete them.

So an auction is still open while it hasn't reached its closing date OR
any of its lots is still open. Lot times come from the lots themselves
(closes_at). Both helpers are SQL clauses over models.Auction, for use in
any query that has Auction in its FROM; the lot subquery uses its own
alias, so it never correlates to a Lot the outer query already selects.
"""

from sqlalchemy import and_, exists, or_
from sqlalchemy.orm import aliased

from .. import models


def _a_lot_still_open(now):
    lot = aliased(models.Lot)
    return exists().where(lot.auction_id == models.Auction.id,
                          lot.closes_at.isnot(None),
                          lot.closes_at >= now)


def still_open(now):
    """No closing date, not reached yet, or a lot of it still open."""
    return or_(models.Auction.closing_date.is_(None),
               models.Auction.closing_date >= now,
               _a_lot_still_open(now))


def is_over(now):
    """Past its closing date with every lot of it closed too."""
    return and_(models.Auction.closing_date.isnot(None),
                models.Auction.closing_date < now,
                ~_a_lot_still_open(now))


def lot_closed(lot, now) -> bool:
    """One lot, in Python: its own closing time decides when it has one;
    the auction's date only for a lot that never had a time of its own."""
    if lot.closes_at is not None:
        return lot.closes_at < now
    return bool(lot.auction and lot.auction.closing_date
                and lot.auction.closing_date < now)
