// The verification links: sold listings only, query encoded, nothing for
// nothing.
import { describe, expect, it } from 'vitest'

import { ebaySoldUrl, kindLabel } from './comps'

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
