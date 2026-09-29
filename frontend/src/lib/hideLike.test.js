// The rule behind the "more like this" offer.
//
// Hiding is 21% of every button press in this app, and half of those presses
// land in runs of three or more - one product dismissed over and over,
// because a liquidation sale lists it once per asset tag. The one-click
// version of that existed for weeks as a dim "+ all" beside the hide button
// and was pressed ONCE against 131 hides. So it now asks after the click
// instead, where the decision was just made.

import { describe, expect, it } from 'vitest'
import { offerFrom, offerLabel } from './hideLike'

const peek = (over = {}) => ({ key: 'dell optiplex 7040', matched: 4, changed: 3,
                               titles: ['Dell OptiPlex 7040 ~ FB-19-71'], ...over })

describe('offerFrom', () => {
  it('offers when others share the product', () => {
    expect(offerFrom(peek())).toEqual({
      key: 'dell optiplex 7040', count: 3,
      titles: ['Dell OptiPlex 7040 ~ FB-19-71'],
    })
  })

  it('says nothing when the lot is one of a kind', () => {
    expect(offerFrom(peek({ changed: 0, matched: 1 }))).toBeNull()
  })

  it('says nothing when the title is too generic to group on', () => {
    // The server returns key: null for these - "Pyrex" is a category, and
    // offering to hide every Pyrex lot because one was rejected is a trap.
    expect(offerFrom(peek({ key: null, changed: 0 }))).toBeNull()
  })

  it('does not ask twice about the same product', () => {
    const answered = new Set(['dell optiplex 7040'])
    expect(offerFrom(peek(), answered)).toBeNull()
  })

  it('still asks about a different product', () => {
    const answered = new Set(['something else entirely'])
    expect(offerFrom(peek(), answered)).not.toBeNull()
  })

  it('survives a peek that failed', () => {
    // The hide itself worked; a broken offer must not throw into the handler.
    for (const bad of [null, undefined, {}, { key: 'x' }]) {
      expect(offerFrom(bad)).toBeNull()
    }
  })

  it('treats a missing titles list as no titles', () => {
    expect(offerFrom(peek({ titles: undefined })).titles).toEqual([])
  })
})

describe('offerLabel', () => {
  it('leads with the count', () => {
    expect(offerLabel({ count: 3 })).toBe('3 more like that one')
  })

  it('reads correctly for one', () => {
    expect(offerLabel({ count: 1 })).toBe('1 more like that one')
  })

  it('groups thousands, because a bad key can match a lot', () => {
    expect(offerLabel({ count: 1200 })).toBe('1,200 more like that one')
  })

  it('is empty with no offer', () => {
    expect(offerLabel(null)).toBe('')
  })
})
