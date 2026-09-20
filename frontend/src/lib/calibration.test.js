// The calibration label: hot when hot, silent when under-evidenced.
import { describe, expect, it } from 'vitest'

import { MIN_OBS, houseRatioLabel, houseRatioTitle } from './calibration'

describe('houseRatioLabel', () => {
  it('names a hot house with one decimal', () => {
    expect(houseRatioLabel(2.53, 41)).toBe('estimates ~2.5x hot')
    expect(houseRatioLabel(1.2, MIN_OBS)).toBe('estimates ~1.2x hot')
  })

  it('calls an honest house honest, and a low one low', () => {
    expect(houseRatioLabel(1.0, 30)).toBe('estimates track results')
    expect(houseRatioLabel(1.19, 30)).toBe('estimates track results')
    expect(houseRatioLabel(0.7, 30)).toBe('estimates run low')
  })

  it('stays silent below the observation floor — anecdotes are not data', () => {
    expect(houseRatioLabel(4.0, MIN_OBS - 1)).toBeNull()
    expect(houseRatioLabel(4.0, 0)).toBeNull()
    expect(houseRatioLabel(null, 100)).toBeNull()
  })
})

describe('houseRatioTitle', () => {
  it('carries the receipts', () => {
    const t = houseRatioTitle(3.8, 52)
    expect(t).toContain('52 observed sales')
    expect(t).toContain('~3.8x')
  })

  it('is silent exactly when the label is', () => {
    expect(houseRatioTitle(4.0, 5)).toBeNull()
  })
})
