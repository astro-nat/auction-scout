// The matching count only applies to the EXACT filter combo it was
// counted under — a stale count from a different scan must fall back to
// null rather than mislabel the button.
import { describe, expect, it } from 'vitest'

import { matchCountFor, importLabel } from './matching'

describe('matchCountFor', () => {
  it('is null with no active filter, even if a count is on file', () => {
    const a = { category_lot_count: 9, category_count_for: -1, category_count_search: null }
    expect(matchCountFor(a, -1, '')).toBeNull()
  })

  it('reads a keyword-only count when nothing else changed', () => {
    const a = { category_lot_count: 4, category_count_for: -1, category_count_search: 'pyrex' }
    expect(matchCountFor(a, -1, 'pyrex')).toBe(4)
    expect(matchCountFor(a, -1, '  pyrex  ')).toBe(4)   // whitespace-insensitive
  })

  it('reads a category-only count exactly as before', () => {
    const a = { category_lot_count: 9, category_count_for: 40252, category_count_search: null }
    expect(matchCountFor(a, 40252, '')).toBe(9)
  })

  it('reads a combined category+keyword count only when both match', () => {
    const a = { category_lot_count: 2, category_count_for: 40252, category_count_search: 'pyrex' }
    expect(matchCountFor(a, 40252, 'pyrex')).toBe(2)
    expect(matchCountFor(a, 40252, '')).toBeNull()      // keyword cleared since
    expect(matchCountFor(a, -1, 'pyrex')).toBeNull()    // category cleared since
    expect(matchCountFor(a, 1, 'pyrex')).toBeNull()     // category changed since
  })

  it('ignores a category-only count once a keyword is typed', () => {
    // The stored count never saw the keyword — showing it now would
    // overstate matches and could wrongly skip a real "0 total" auction.
    const a = { category_lot_count: 30, category_count_for: 40252, category_count_search: null }
    expect(matchCountFor(a, 40252, 'pyrex')).toBeNull()
  })

  it('is null when nothing has ever been counted', () => {
    const a = {}
    expect(matchCountFor(a, 40252, 'pyrex')).toBeNull()
  })
})

describe('importLabel', () => {
  const withCount = (n) =>
    ({ category_lot_count: n, category_count_for: -1, category_count_search: 'pyrex' })

  it('is plain "Import" with no usable count', () => {
    expect(importLabel({})).toBe('Import')
  })

  it('names the keyword when only search is active', () => {
    expect(importLabel(withCount(4), { categoryId: -1, searchText: 'pyrex' }))
      .toBe('Import 4 "pyrex"')
  })

  it('names the category when only category is active', () => {
    const a = { category_lot_count: 9, category_count_for: 40252, category_count_search: null }
    expect(importLabel(a, { categoryId: 40252, categoryName: 'Antiques', searchText: '' }))
      .toBe('Import 9 Antiques')
  })

  it('names both when they compose', () => {
    const a = { category_lot_count: 2, category_count_for: 40252, category_count_search: 'pyrex' }
    expect(importLabel(a, { categoryId: 40252, categoryName: 'Antiques', searchText: 'pyrex' }))
      .toBe('Import 2 Antiques · "pyrex"')
  })

  it('shortens to just the count on mobile', () => {
    const a = { category_lot_count: 2, category_count_for: 40252, category_count_search: 'pyrex' }
    expect(importLabel(a, { categoryId: 40252, categoryName: 'Antiques',
                            searchText: 'pyrex', mobile: true })).toBe('Import 2')
  })

  it('falls back to a generic category label with none named', () => {
    const a = { category_lot_count: 9, category_count_for: 40252, category_count_search: null }
    expect(importLabel(a, { categoryId: 40252, searchText: '' })).toBe('Import 9 matching')
  })
})
