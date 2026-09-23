import { describe, expect, it } from 'vitest'
import { MONEY_RANGES, ROI_RANGES, matchesFilter, roiPercent } from './filters'

describe('matchesFilter', () => {
  it('reads > as more than and < as less than', () => {
    expect(matchesFilter(76, '>50')).toBe(true)
    expect(matchesFilter(40, '>50')).toBe(false)
    expect(matchesFilter(4, '<5')).toBe(true)
    expect(matchesFilter(5, '<5')).toBe(false)
  })

  it('comparisons never match a missing value', () => {
    expect(matchesFilter(null, '>0')).toBe(false)
    expect(matchesFilter(undefined, '<100')).toBe(false)
  })

  it('an empty query matches everything', () => {
    expect(matchesFilter(null, '')).toBe(true)
    expect(matchesFilter('anything', undefined)).toBe(true)
  })

  it('a plain query is a case-insensitive substring', () => {
    expect(matchesFilter('Swarovski SCS Egg', 'scs')).toBe(true)
    expect(matchesFilter('Swarovski SCS Egg', 'lego')).toBe(false)
  })
})

describe('the ROI presets', () => {
  it('are all "at least", where the money presets are all "under"', () => {
    // The ask: the ROI filter offered "<" and only ">" makes sense for it.
    expect(ROI_RANGES.every((r) => r.startsWith('>'))).toBe(true)
    expect(MONEY_RANGES.every((r) => r.startsWith('<'))).toBe(true)
  })

  it('work in percent, the units the cell shows', () => {
    // est_roi is stored as a ratio. Filtering the ratio with the money
    // presets made "<5" mean "under 500%", which matched every lot.
    expect(roiPercent(0.76)).toBe(76)
    expect(roiPercent(-0.8901)).toBe(-89)
    expect(roiPercent(null)).toBeNull()
    expect(matchesFilter(roiPercent(0.76), '>50')).toBe(true)
    expect(matchesFilter(roiPercent(0.76), '>100')).toBe(false)
    expect(matchesFilter(roiPercent(1.5), '>100')).toBe(true)
  })
})
