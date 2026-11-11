import { describe, it, expect } from 'vitest'
import { strLabel, strTitle } from './sellThrough'

describe('sell-through label', () => {
  it('rounds to a whole percent', () => {
    expect(strLabel({ str_pct: 63.2 })).toBe('STR 63%')
  })
  it('shows nothing before a check', () => {
    expect(strLabel({})).toBe(null)
    expect(strTitle({})).toBe(undefined)
  })
  it('names the counts behind it', () => {
    expect(strTitle({ str_pct: 60, str_sold: 1200, str_active: 800 }))
      .toContain('1,200 sold vs 800 listed')
  })
})
