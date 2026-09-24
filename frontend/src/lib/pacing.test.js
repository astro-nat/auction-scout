import { describe, expect, it } from 'vitest'
import { auctionClosed, goldBadge } from './pacing'

const NOW = new Date('2026-09-20T12:00:00Z')
const auction = (over = {}) => ({
  gold_count: 2, gold_profit: 275, lots_enriched: 10,
  closing_date: '2026-09-22T12:00:00', ...over,
})

describe('auctionClosed', () => {
  it('is closed only once the close time has passed', () => {
    expect(auctionClosed(auction({ closing_date: '2026-09-19T12:00:00' }), NOW)).toBe(true)
    expect(auctionClosed(auction(), NOW)).toBe(false)
  })

  it('treats a missing close time as open', () => {
    expect(auctionClosed(auction({ closing_date: null }), NOW)).toBe(false)
  })
})

describe('goldBadge', () => {
  it('shows count and profit', () => {
    expect(goldBadge(auction())).toBe('2 gold · ~$275 potential profit')
  })

  it('shows nothing without gold', () => {
    expect(goldBadge(auction({ gold_count: 0 }))).toBeNull()
    expect(goldBadge(auction({ gold_count: undefined }))).toBeNull()
  })
})
