// Acquisition-pacing verdicts for the auctions list — pure so they're
// testable, since these decide what gets highlighted and what gets dimmed.
//
// The floor logic in one breath: every auction costs a fixed overhead (a
// shipping minimum or a pickup trip), so an auction must put at least the
// floor of audit-trusted gold-mine profit on the table to be worth touching.
// Below the floor WITH enrichment done means measured-and-found-thin; an
// un-enriched auction is unknown, not thin, and is never dimmed.
import { parseUtc } from './time'

export function auctionClosed(a, now = new Date()) {
  return Boolean(a.closing_date && parseUtc(a.closing_date) < now)
}

// gold_profit sums audit-surviving GOLD MINE lots only, so this is the
// trusted number; the floor is inclusive (an exactly-$200 auction clears).
export function clearsFloor(a, floor) {
  return Number(a.gold_profit ?? 0) >= floor
}

export function underFloor(a, floor, now = new Date()) {
  return (a.lots_enriched ?? 0) > 0 && !clearsFloor(a, floor)
    && !auctionClosed(a, now)
}

export function goldBadge(a, floor, now = new Date()) {
  if (!(a.gold_count > 0)) return null
  return `🟢 ${a.gold_count} gold · ~$${Number(a.gold_profit).toFixed(0)} potential profit`
    + (underFloor(a, floor, now) ? ` · under $${floor} floor` : '')
}
