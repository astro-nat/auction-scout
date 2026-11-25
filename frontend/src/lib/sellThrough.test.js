import { describe, it, expect } from 'vitest'
import { STR_METHOD, strLabel, strTitle } from './sellThrough'

describe('sell-through label', () => {
  const e = { str_pct: 63.2, str_sold: 12, str_active: 7, str_method: STR_METHOD }
  it('rounds to a whole percent', () => {
    expect(strLabel(e)).toBe('STR 63%')
  })
  it('shows nothing before a check', () => {
    expect(strLabel({})).toBe(null)
    expect(strTitle({})).toBe(undefined)
  })
  it('hides a reading from an older method', () => {
    expect(strLabel({ ...e, str_method: null })).toBe(null)
    expect(strLabel({ ...e, str_method: 'totals-v1' })).toBe(null)
  })
  it('names the counts behind it', () => {
    expect(strTitle({ ...e, str_sold: 1200, str_active: 800 }))
      .toContain('1,200 sold in the last 90 days vs 800 listed now')
  })
})
