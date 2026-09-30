// Lots grouped by the auction they belong to, for the "By auction" view.
//
// The reason to group: a pickup trip is a fixed cost. Once you're driving to
// an auction for a gold mine, a lot there that would never justify the trip
// on its own is worth adding. So a lot's verdict depends on its neighbours:
// at an auction with an open gold mine, anything clearing a lower ROI bar is
// an ADD-ON; the same lot anywhere else is still a pass.
//
// Pure - no React, no fetch - so the rules are tested without a DOM.

const isClosed = (lot) => Boolean(lot.item_closed ?? lot.auction_closed)
const isGold = (lot) => lot.enrichment?.roi_status === 'GOLD MINE'

// One lot's role inside its group:
//   gold      GOLD MINE, as graded by the server
//   addon     open, priced, and its ROI at the current bid clears the add-on
//             bar - only at an auction with an open gold mine
//   over      bid has passed the max-bid ceiling (and it isn't an add-on)
//   unpriced  no ROI yet
//   pass      everything else, closed lots included
//
// Add-on is checked BEFORE over on purpose: max_bid is the ceiling at the
// gold-mine target, so an add-on's bid is often above it by design. Checked
// the other way round, every add-on would read as "over your max".
export function lotKind(lot, { groupHasGold, addonFloorPct, isOverbid = () => false }) {
  const e = lot.enrichment || {}
  if (isGold(lot)) return 'gold'
  if (e.est_roi == null) return 'unpriced'
  const open = !isClosed(lot)
  if (open && groupHasGold && Number(e.est_roi) * 100 >= addonFloorPct) return 'addon'
  if (isOverbid(lot, e)) return 'over'
  return 'pass'
}

const closeTime = (lot) => {
  const t = lot.closes_at ? Date.parse(lot.closes_at) : NaN
  return Number.isNaN(t) ? Infinity : t
}

// `lots` arrive already filtered and sorted the way the user asked; each
// group keeps that order inside its featured and other lists.
//
// Groups: auctions with an open gold mine first, then by the soonest open
// lot to close, then by name. `auctions` maps auction id to the imported
// auction's row (city, source, buyer premium...) where App has one.
export function groupByAuction(lots, { auctions = {}, addonFloorPct = 50, isOverbid } = {}) {
  const byId = new Map()
  for (const lot of lots) {
    const id = lot.auction_id ?? 'none'
    if (!byId.has(id)) byId.set(id, [])
    byId.get(id).push(lot)
  }

  const groups = [...byId.entries()].map(([id, members]) => {
    const groupHasGold = members.some((l) => isGold(l) && !isClosed(l))
    const kinds = new Map(members.map((l) =>
      [l.lot_id, lotKind(l, { groupHasGold, addonFloorPct, isOverbid })]))
    const golds = members.filter((l) => kinds.get(l.lot_id) === 'gold')
    const addons = members.filter((l) => kinds.get(l.lot_id) === 'addon')
    const basket = [...golds, ...addons]
    const others = members.filter((l) => !['gold', 'addon'].includes(kinds.get(l.lot_id)))
    const open = members.filter((l) => !isClosed(l))
    const rois = members.map((l) => l.enrichment?.est_roi).filter((r) => r != null).map(Number)
    const sum = (list, get) => list.reduce((s, l) => s + (Number(get(l)) || 0), 0)
    return {
      auctionId: id === 'none' ? null : id,
      name: members[0].auction_name || auctions[id]?.name || 'Unknown auction',
      auction: auctions[id] || null,
      lots: members,
      kinds,
      hasGold: groupHasGold,
      goldCount: golds.length,
      addonCount: addons.length,
      featured: basket,
      others,
      // What the gold mines and add-ons cost at today's bids, and what
      // they're expected to resell for. Current bids, not max bids: an
      // add-on's max_bid is the gold-target ceiling, not what it's worth
      // paying as an add-on.
      basketBids: sum(basket, (l) => l.current_bid),
      basketResale: sum(basket, (l) => l.enrichment?.est_resale),
      bestRoi: rois.length ? Math.max(...rois) : null,
      firstClose: open.length ? Math.min(...open.map(closeTime)) : Infinity,
    }
  })

  return groups.sort((a, b) =>
    (b.hasGold - a.hasGold)
    || (a.firstClose - b.firstClose)
    || String(a.name).localeCompare(String(b.name)))
}

// "2 gold mines + 1 add-on"
export function basketLabel(goldCount, addonCount) {
  const g = `${goldCount} gold ${goldCount === 1 ? 'mine' : 'mines'}`
  if (!addonCount) return g
  return `${g} + ${addonCount} ${addonCount === 1 ? 'add-on' : 'add-ons'}`
}

// Per-browser preferences, the way page size is kept. Storage can be
// missing or throw (private windows); the defaults stand in.
export const ARRANGE_KEY = 'auctionscout.arrange'
export const ADDON_FLOOR_KEY = 'auctionscout.addonFloorPct'
export const DEFAULT_ADDON_FLOOR = 50

export function savedArrange(storage) {
  try {
    const v = storage?.getItem(ARRANGE_KEY)
    return v === 'all' || v === 'auction' ? v : 'auction'
  } catch { return 'auction' }
}

export function savedAddonFloor(storage) {
  try {
    const raw = storage?.getItem(ADDON_FLOOR_KEY)
    const n = raw == null || raw === '' ? NaN : Number(raw)
    return Number.isFinite(n) && n >= 0 ? n : DEFAULT_ADDON_FLOOR
  } catch { return DEFAULT_ADDON_FLOOR }
}

export function savePref(storage, key, value) {
  try { storage?.setItem(key, String(value)) } catch { /* a preference, not data */ }
}
