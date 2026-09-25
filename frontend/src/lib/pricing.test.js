import { describe, expect, it } from 'vitest'
import { aiDone, rowAction } from './pricing'

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
