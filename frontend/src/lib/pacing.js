// Auction-row checks for the auctions list — pure so they're testable.
import { parseUtc } from './time'

export function auctionClosed(a, now = new Date()) {
  return Boolean(a.closing_date && parseUtc(a.closing_date) < now)
}
