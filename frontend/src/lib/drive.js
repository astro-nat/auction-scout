// Which auctions are too far to drive to, for the "Hide auctions more than
// N min away" rule. Pure, so the rule is tested without a DOM.
//
// Ship-only sales are never "too far": a far auction that ships is not a
// drive at all, and hiding it would hide lots that can still be had by
// mail. An auction with no drive time yet is kept - unknown is not far.
//
// Nor is one with an open gold mine (`worthTheTrip`): a gold mine is what
// makes a longer drive pay, the same reasoning as add-ons. The rule went
// live hiding a 36-minute estate sale holding ten of them.
export function farAuctionIds(auctions, maxMinutes, worthTheTrip = new Set()) {
  const limit = Number(maxMinutes)
  return new Set(auctions
    .filter((a) => a && a.source !== 'Ship' && a.drive_minutes != null
                   && Number(a.drive_minutes) > limit && !worthTheTrip.has(a.id))
    .map((a) => a.id))
}

// Auctions over the limit that a gold mine keeps in view, for the rule's
// count - so "kept" is said, not silent.
export function keptForGold(auctions, maxMinutes, worthTheTrip) {
  const limit = Number(maxMinutes)
  return auctions.filter((a) => a && a.source !== 'Ship' && a.drive_minutes != null
    && Number(a.drive_minutes) > limit && worthTheTrip.has(a.id)).length
}
