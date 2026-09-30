import { describe, expect, it } from 'vitest'
import { formatDrive } from '../components/DriveFrom'

describe('formatDrive', () => {
  it('reads as minutes under an hour and hours past it', () => {
    expect(formatDrive(null)).toBe(null)
    expect(formatDrive(11.4)).toBe('11 min drive')
    expect(formatDrive(59.6)).toBe('1 h drive')
    expect(formatDrive(84)).toBe('1 h 24 min drive')
    expect(formatDrive(120)).toBe('2 h drive')
  })
})

import { farAuctionIds } from './drive'

describe('farAuctionIds', () => {
  const auctions = [
    { id: 1, source: 'Local Pickup', drive_minutes: 24 },
    { id: 2, source: 'Local Pickup', drive_minutes: 41 },
    { id: 3, source: 'Ship', drive_minutes: 600 },
    { id: 4, source: 'Local Pickup', drive_minutes: null },
    { id: 5, source: 'Local Pickup', drive_minutes: 30 },
  ]

  it('hides pickup auctions past the limit, and only those', () => {
    expect([...farAuctionIds(auctions, 30)]).toEqual([2])
  })

  it('never hides an auction that ships, however far', () => {
    expect(farAuctionIds(auctions, 10).has(3)).toBe(false)
  })

  it('keeps an auction with no drive time yet', () => {
    expect(farAuctionIds(auctions, 0).has(4)).toBe(false)
  })

  it('moves with the limit', () => {
    expect([...farAuctionIds(auctions, 20)].sort()).toEqual([1, 2, 5])
    expect(farAuctionIds(auctions, 60).size).toBe(0)
  })
})

describe('a gold mine is worth the drive', () => {
  const auctions = [
    { id: 1, source: 'Local Pickup', drive_minutes: 36 },   // the estate sale
    { id: 2, source: 'Local Pickup', drive_minutes: 42 },
    { id: 3, source: 'Local Pickup', drive_minutes: 12 },
  ]

  it('never hides an auction with an open gold mine, however far', () => {
    const gold = new Set([1])
    expect([...farAuctionIds(auctions, 30, gold)]).toEqual([2])
  })

  it('says how many it kept for their gold', async () => {
    const { keptForGold } = await import('./drive')
    expect(keptForGold(auctions, 30, new Set([1]))).toBe(1)
    expect(keptForGold(auctions, 30, new Set([3]))).toBe(0)
  })
})
