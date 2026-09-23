import { describe, expect, it } from 'vitest'
import { CLOSING_RANGES, MONEY_RANGES, ROI_RANGES, hoursUntil, matchesFilter, optionOf, roiPercent } from './filters'

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

describe('closing-time presets', () => {
  const NOW = Date.parse('2026-09-23T12:00:00Z')
  const at = (hours) => new Date(NOW + hours * 3600000).toISOString()
  const inWindow = (hours, preset) => matchesFilter(hoursUntil(at(hours), NOW), preset)

  it('measure hours until close', () => {
    expect(hoursUntil(at(3), NOW)).toBeCloseTo(3)
    expect(hoursUntil(at(0.5), NOW)).toBeCloseTo(0.5)
  })

  it('treat a closed or unknown close time as nothing to count down to', () => {
    // Null matches no preset: "< 1 hr" means closing within the hour,
    // not closed an hour ago - you cannot bid on that.
    expect(hoursUntil(at(-1), NOW)).toBeNull()
    expect(hoursUntil(null, NOW)).toBeNull()
    expect(hoursUntil('not a date', NOW)).toBeNull()
    expect(matchesFilter(hoursUntil(at(-1), NOW), '<1')).toBe(false)
  })

  it('bucket the way the labels promise', () => {
    expect(inWindow(0.5, '<1')).toBe(true)
    expect(inWindow(0.5, '<24')).toBe(true)
    expect(inWindow(0.5, '>168')).toBe(false)
    expect(inWindow(30, '<24')).toBe(false)
    expect(inWindow(30, '<48')).toBe(true)
    expect(inWindow(6 * 24, '<168')).toBe(true)
    expect(inWindow(10 * 24, '<168')).toBe(false)
    expect(inWindow(10 * 24, '>168')).toBe(true)
  })

  it('carry a label distinct from the value they compare', () => {
    expect(CLOSING_RANGES.map((r) => optionOf(r).label)).toEqual(
      ['< 1 hr', '< 24 hrs', '< 2 days', '< 7 days', '> 7 days'])
    expect(CLOSING_RANGES.map((r) => optionOf(r).value)).toEqual(
      ['<1', '<24', '<48', '<168', '>168'])
    // Money presets stay plain strings and normalise to themselves.
    expect(optionOf('<5')).toEqual({ value: '<5', label: '<5' })
  })
})
