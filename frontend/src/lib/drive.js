// Which auctions are too far to drive to, for the "Hide auctions more than
// N min away" rule. Pure, so the rule is tested without a DOM.
//
// Ship-only sales are never "too far": a far auction that ships is not a
// drive at all, and hiding it would hide lots that can still be had by
// mail. An auction with no drive time yet is kept - unknown is not far.
export function farAuctionIds(auctions, maxMinutes) {
  const limit = Number(maxMinutes)
  return new Set(auctions
    .filter((a) => a && a.source !== 'Ship' && a.drive_minutes != null
                   && Number(a.drive_minutes) > limit)
    .map((a) => a.id))
}
