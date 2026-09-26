import { describe, expect, it } from 'vitest'
import { aiDone, rowAction, unpricedAcross } from './pricing'

describe('aiDone', () => {
  it('is a success the AI actually saw', () => {
    expect(aiDone({ status: 'success', ai_source: 'vision' })).toBe(true)
    expect(aiDone({ status: 'success', ai_source: 'vision-itemized' })).toBe(true)
  })

  it('is not a success the AI never saw, nor a comps-only price', () => {
    expect(aiDone({ status: 'success', ai_source: 'none' })).toBe(false)
    expect(aiDone({ status: 'success', ai_source: null })).toBe(false)
    expect(aiDone({ status: 'pending', est_resale: 12 })).toBe(false)
    expect(aiDone(null)).toBe(false)
  })
})

describe('rowAction', () => {
  it('offers comps first on an unpriced lot', () => {
    expect(rowAction({ status: 'pending', est_resale: null })).toMatchObject(
      { step: 'comps', label: 'Price with comps', disabled: false })
  })

  it('offers AI once comps have priced it', () => {
    expect(rowAction({ status: 'pending', est_resale: 40 })).toMatchObject(
      { step: 'ai', label: 'Further inspect with AI', disabled: false })
  })

  it('locks a lot AI has priced', () => {
    expect(rowAction({ status: 'success', ai_source: 'text', est_resale: 40 })).toMatchObject(
      { step: 'locked', disabled: true })
  })

  it('lets a failed lot, or one the AI never saw, try AI again', () => {
    expect(rowAction({ status: 'failed', est_resale: 40 }).step).toBe('ai')
    expect(rowAction({ status: 'success', ai_source: 'none', est_resale: 40 }).step).toBe('ai')
  })

  it('is busy while anything is running on it', () => {
    expect(rowAction({ status: 'queued' }).disabled).toBe(true)
    expect(rowAction({ status: 'pending' }, true).step).toBe('working')
  })
})

describe('unpricedAcross', () => {
  const A = (id, unpriced, closed = false) => ({ id, lots_unpriced: unpriced, closed })
  const isClosed = (a) => !!a.closed

  it('adds up what is waiting and names the auctions', () => {
    const r = unpricedAcross([A(1, 20), A(2, 5), A(3, 0)], isClosed)
    expect(r).toEqual({ lots: 25, auctions: 2, ids: [1, 2] })
  })

  it('leaves out a closed auction — nothing in it can be bought', () => {
    const r = unpricedAcross([A(1, 20), A(2, 99, true)], isClosed)
    expect(r).toEqual({ lots: 20, auctions: 1, ids: [1] })
  })

  it('counts nothing when everything is priced', () => {
    expect(unpricedAcross([A(1, 0), A(2, 0)], isClosed))
      .toEqual({ lots: 0, auctions: 0, ids: [] })
  })

  it('survives missing fields and an empty list', () => {
    expect(unpricedAcross([{ id: 9 }, null], isClosed).lots).toBe(0)
    expect(unpricedAcross([]).lots).toBe(0)
    expect(unpricedAcross(undefined).lots).toBe(0)
  })

  it('treats every auction as open when no closed test is given', () => {
    expect(unpricedAcross([A(1, 4, true)]).lots).toBe(4)
  })
})
