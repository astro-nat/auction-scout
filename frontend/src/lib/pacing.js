// Auction-row verdicts for the auctions list — pure so they're testable.
import { parseUtc } from './time'

export function auctionClosed(a, now = new Date()) {
  return Boolean(a.closing_date && parseUtc(a.closing_date) < now)
}

// gold_profit sums audit-surviving GOLD MINE lots only, so this is the
// trusted number.
export function goldBadge(a) {
  if (!(a.gold_count > 0)) return null
  return `${a.gold_count} gold · ~$${Number(a.gold_profit).toFixed(0)} potential profit`
}
