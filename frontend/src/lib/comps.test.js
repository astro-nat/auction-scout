// The verification links: sold listings only, query encoded, nothing for
// nothing.
import { describe, expect, it } from 'vitest'

import { compRows, ebaySoldUrl, kindLabel } from './comps'

describe('ebaySoldUrl', () => {
  it('builds a completed+sold search for the title', () => {
    const url = ebaySoldUrl('Navajo Sterling Turquoise Necklace')
    expect(url).toContain('LH_Sold=1')
    expect(url).toContain('LH_Complete=1')
    expect(url).toContain('_nkw=Navajo%20Sterling%20Turquoise%20Necklace')
  })

  it('encodes characters that would break the query', () => {
    expect(ebaySoldUrl('14" Wall Light & Mount / 2-pack'))
      .toContain('_nkw=14%22%20Wall%20Light%20%26%20Mount%20%2F%202-pack')
  })

  it('returns null for empty titles rather than a search for nothing', () => {
    expect(ebaySoldUrl('')).toBeNull()
    expect(ebaySoldUrl('   ')).toBeNull()
    expect(ebaySoldUrl(null)).toBeNull()
  })
})

describe('kindLabel', () => {
  it('names the provenance, defaulting unknowns to sold', () => {
    expect(kindLabel('sold')).toBe('sold')
    expect(kindLabel('asking')).toBe('asking')
    expect(kindLabel('retail')).toBe('retail')
    expect(kindLabel(undefined)).toBe('sold')   // legacy records carry no kind
  })
})

describe('compRows', () => {
  it('formats price, provenance and a truncated date into the label', () => {
    const [row] = compRows([{ price: 45, title: 'Widget mint',
                              url: 'https://ebay.com/itm/2',
                              date: '2026-08-20T14:03:00Z', kind: 'sold' }])
    expect(row.label).toBe('$45.00 sold 2026-08-20')   // datetime cut to date
    expect(row.title).toBe('Widget mint')
    expect(row.url).toBe('https://ebay.com/itm/2')
  })

  it('omits the date when there is none and nulls a missing link', () => {
    const [row] = compRows([{ price: 42.5, title: 'Widget boxed', kind: 'asking' }])
    expect(row.label).toBe('$42.50 asking')
    expect(row.url).toBeNull()
  })

  it('shrugs at nothing: null, undefined and empty all give no rows', () => {
    expect(compRows(null)).toEqual([])
    expect(compRows(undefined)).toEqual([])
    expect(compRows([])).toEqual([])
  })

  it('survives a record with a missing title', () => {
    expect(compRows([{ price: 10, kind: 'sold' }])[0].title).toBe('')
  })
})
