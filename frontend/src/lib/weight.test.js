import { describe, it, expect } from 'vitest'
import { weightLabel } from './weight'

describe('weightLabel', () => {
  it('marks the item-type guess as rough', () => {
    expect(weightLabel({ weight_lb: 4, weight_source: 'est' })).toBe('~4 lb')
  })
  it('shows an AI weight plainly', () => {
    expect(weightLabel({ weight_lb: 5.5, weight_source: 'ai' })).toBe('5.5 lb')
  })
  it('reads under a pound in ounces', () => {
    expect(weightLabel({ weight_lb: 0.2, weight_source: 'est' })).toBe('~3 oz')
  })
  it('rounds heavy items to the pound', () => {
    expect(weightLabel({ weight_lb: 40, weight_source: 'est' })).toBe('~40 lb')
  })
  it('shows nothing without a weight', () => {
    expect(weightLabel({})).toBe(null)
    expect(weightLabel(undefined)).toBe(null)
  })
})
