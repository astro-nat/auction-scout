import { describe, expect, it } from 'vitest'
import { basketLabel, groupByAuction, lotKind, savedAddonFloor, savedArrange } from './grouping'

let seq = 0
function lot(auctionId, { roi = null, gold = false, bid = 5, max = 10, resale = 40,
  closes = '2026-10-01T20:00:00Z', closed = false } = {}) {
  seq += 1
  return {
    lot_id: seq, auction_id: auctionId, auction_name: `Auction ${auctionId}`,
    current_bid: bid, closes_at: closes, item_closed: closed,
    enrichment: {
      est_roi: roi, max_bid: max, est_resale: resale,
      roi_status: gold ? 'GOLD MINE' : (roi == null ? null : 'PASS'),
    },
  }
}
const overbid = (l, e) => Number(l.current_bid) > Number(e.max_bid)

describe('lotKind', () => {
  const opts = { groupHasGold: true, addonFloorPct: 50, isOverbid: overbid }

  it('is an add-on at an auction with a gold mine when ROI clears the bar', () => {
    expect(lotKind(lot(1, { roi: 0.6 }), opts)).toBe('addon')
  })

  it('stays a pass at an auction without a gold mine', () => {
    expect(lotKind(lot(1, { roi: 0.6 }), { ...opts, groupHasGold: false })).toBe('pass')
  })

  it('misses the bar just below it', () => {
    expect(lotKind(lot(1, { roi: 0.48 }), opts)).toBe('pass')
  })

  it('checks add-on before over-your-max', () => {
    // max_bid is the ceiling at the GOLD target; an add-on is expected to
    // sit above it and still clear the lower bar.
    expect(lotKind(lot(1, { roi: 3.0, bid: 12, max: 7 }), opts)).toBe('addon')
    expect(lotKind(lot(1, { roi: 0.2, bid: 12, max: 7 }), opts)).toBe('over')
  })

  it('never makes a closed lot an add-on', () => {
    expect(lotKind(lot(1, { roi: 2, closed: true }), opts)).toBe('pass')
  })

  it('leaves gold and unpriced lots as they are', () => {
    expect(lotKind(lot(1, { roi: 6, gold: true }), opts)).toBe('gold')
    expect(lotKind(lot(1), opts)).toBe('unpriced')
  })
})

describe('groupByAuction', () => {
  it('puts auctions with a gold mine first, then the soonest to close', () => {
    const lots = [
      lot(1, { roi: 0.9, closes: '2026-10-01T18:00:00Z' }),
      lot(2, { roi: 6, gold: true, closes: '2026-10-02T18:00:00Z' }),
      lot(3, { roi: 0.9, closes: '2026-10-01T12:00:00Z' }),
    ]
    const groups = groupByAuction(lots, { isOverbid: overbid })
    expect(groups.map((g) => g.auctionId)).toEqual([2, 3, 1])
  })

  it('features gold then add-ons, keeping the incoming order within each', () => {
    const a = lot(1, { roi: 1.2 })
    const g = lot(1, { roi: 6, gold: true })
    const b = lot(1, { roi: 0.7 })
    const p = lot(1, { roi: 0.1 })
    const [group] = groupByAuction([a, g, b, p], { isOverbid: overbid })
    expect(group.featured.map((l) => l.lot_id)).toEqual([g.lot_id, a.lot_id, b.lot_id])
    expect(group.others.map((l) => l.lot_id)).toEqual([p.lot_id])
    expect([group.goldCount, group.addonCount]).toEqual([1, 2])
  })

  it('totals the basket at current bids and expected resale', () => {
    const [group] = groupByAuction([
      lot(1, { roi: 6, gold: true, bid: 12, resale: 148 }),
      lot(1, { roi: 1.85, bid: 6, resale: 42 }),
      lot(1, { roi: 0.1, bid: 99, resale: 5 }),
    ], { isOverbid: overbid })
    expect(group.basketBids).toBe(18)
    expect(group.basketResale).toBe(190)
  })

  it('a closed gold mine does not turn its neighbours into add-ons', () => {
    const [group] = groupByAuction([
      lot(1, { roi: 6, gold: true, closed: true }),
      lot(1, { roi: 1.5 }),
    ], { isOverbid: overbid })
    expect(group.hasGold).toBe(false)
    expect(group.addonCount).toBe(0)
  })

  it('moves the add-on bar with the setting', () => {
    const lots = [lot(1, { roi: 6, gold: true }), lot(1, { roi: 0.8 })]
    expect(groupByAuction(lots, { addonFloorPct: 50 })[0].addonCount).toBe(1)
    expect(groupByAuction(lots, { addonFloorPct: 100 })[0].addonCount).toBe(0)
  })

  it('takes the auction row from App when there is one', () => {
    const [group] = groupByAuction([lot(7, { roi: 1 })],
      { auctions: { 7: { id: 7, city: 'Pearland', buyer_premium_mult: 1.15 } } })
    expect(group.auction.city).toBe('Pearland')
  })
})

describe('labels and preferences', () => {
  it('names the basket', () => {
    expect(basketLabel(1, 0)).toBe('1 gold mine')
    expect(basketLabel(2, 1)).toBe('2 gold mines + 1 add-on')
    expect(basketLabel(1, 3)).toBe('1 gold mine + 3 add-ons')
  })

  it('falls back to defaults when storage is empty, junk or throws', () => {
    const throwing = { getItem: () => { throw new Error('blocked') } }
    expect(savedArrange(null)).toBe('auction')
    expect(savedArrange({ getItem: () => 'nonsense' })).toBe('auction')
    expect(savedArrange({ getItem: () => 'all' })).toBe('all')
    expect(savedArrange(throwing)).toBe('auction')
    expect(savedAddonFloor({ getItem: () => '120' })).toBe(120)
    expect(savedAddonFloor({ getItem: () => 'x' })).toBe(50)
    expect(savedAddonFloor(throwing)).toBe(50)
    expect(savedAddonFloor({ getItem: () => '0' })).toBe(0)
    expect(savedAddonFloor({ getItem: () => null })).toBe(50)
  })
})
