// The floor verdicts: what gets highlighted, what gets dimmed, and — the
// rule most worth pinning — what never gets dimmed because it hasn't been
// measured yet. Unknown is not thin.
import { describe, expect, it } from 'vitest'

import { auctionClosed, clearsFloor, goldBadge, underFloor } from './pacing'

const NOW = new Date('2026-09-20T12:00:00Z')
const OPEN = '2026-09-22T01:00:00'    // naive UTC, as the backend serves it
const CLOSED = '2026-09-19T01:00:00'

const auction = (over = {}) => ({
  closing_date: OPEN, lot_count: 100,
  lots_imported: 100, lots_enriched: 100,
  gold_count: 3, gold_profit: 275,
  ...over,
})

describe('clearsFloor', () => {
  it('is inclusive: exactly the floor clears it', () => {
    expect(clearsFloor(auction({ gold_profit: 200 }), 200)).toBe(true)
    expect(clearsFloor(auction({ gold_profit: 199.99 }), 200)).toBe(false)
  })

  it('treats missing gold_profit as zero', () => {
    expect(clearsFloor(auction({ gold_profit: null }), 200)).toBe(false)
    expect(clearsFloor(auction({ gold_profit: undefined }), 0)).toBe(true)
  })

  it('a floor of zero highlights everything measured', () => {
    expect(clearsFloor(auction({ gold_profit: 0 }), 0)).toBe(true)
  })

  it('handles the API serving numerics as strings', () => {
    expect(clearsFloor(auction({ gold_profit: '275.00' }), 200)).toBe(true)
  })
})

describe('underFloor', () => {
  it('dims an enriched open auction below the floor', () => {
    expect(underFloor(auction({ gold_profit: 22 }), 200, NOW)).toBe(true)
  })

  it('never dims the un-enriched: unknown is not thin', () => {
    expect(underFloor(auction({ gold_profit: 0, lots_enriched: 0 }), 200, NOW))
      .toBe(false)
    expect(underFloor(auction({ gold_profit: 0, lots_enriched: null }), 200, NOW))
      .toBe(false)
    expect(underFloor(auction({ gold_profit: 0, lots_enriched: undefined }), 200, NOW))
      .toBe(false)
  })

  it('never dims a closed auction — the verdict no longer matters', () => {
    expect(underFloor(auction({ gold_profit: 22, closing_date: CLOSED }), 200, NOW))
      .toBe(false)
  })

  it('never dims what clears the floor', () => {
    expect(underFloor(auction(), 200, NOW)).toBe(false)
  })
})

describe('auctionClosed', () => {
  it('compares naive-UTC closing dates against the given now', () => {
    expect(auctionClosed(auction(), NOW)).toBe(false)
    expect(auctionClosed(auction({ closing_date: CLOSED }), NOW)).toBe(true)
  })

  it('an auction without a closing date is never closed', () => {
    expect(auctionClosed(auction({ closing_date: null }), NOW)).toBe(false)
  })
})

describe('goldBadge', () => {
  it('is absent without gold mines', () => {
    expect(goldBadge(auction({ gold_count: 0, gold_profit: 0 }), 200, NOW))
      .toBeNull()
  })

  it('shows count and profit, no floor note when it clears', () => {
    expect(goldBadge(auction(), 200, NOW))
      .toBe('3 gold · ~$275 potential profit')
  })

  it('names the configured floor when measured thin', () => {
    expect(goldBadge(auction({ gold_count: 1, gold_profit: 22 }), 200, NOW))
      .toBe('1 gold · ~$22 potential profit · under $200 floor')
    expect(goldBadge(auction({ gold_count: 1, gold_profit: 22 }), 300, NOW))
      .toContain('under $300 floor')
  })

  it('drops the floor note once the auction is closed', () => {
    expect(goldBadge(auction({ gold_count: 1, gold_profit: 22,
                               closing_date: CLOSED }), 200, NOW))
      .toBe('1 gold · ~$22 potential profit')
  })
})
