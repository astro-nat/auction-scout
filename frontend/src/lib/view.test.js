import { describe, expect, it } from 'vitest'
import {
  DEFAULT_VIEW, VIEWS, VIEW_KEY, initialView, saveView, viewFromHash, viewUrl,
} from './view'

// A fake localStorage. The throwing variant is not hypothetical: Safari in
// private mode throws on setItem, and some privacy extensions throw on read
// too, which is why every access in the app is wrapped.
function storage(initial = {}) {
  const data = { ...initial }
  return {
    getItem: (k) => (k in data ? data[k] : null),
    setItem: (k, v) => { data[k] = String(v) },
    data,
  }
}

const hostile = {
  getItem() { throw new Error('storage disabled') },
  setItem() { throw new Error('storage disabled') },
}

describe('viewFromHash', () => {
  it('reads a known view out of the hash', () => {
    expect(viewFromHash('#items')).toBe('items')
    expect(viewFromHash('#auctions')).toBe('auctions')
    expect(viewFromHash('#saved')).toBe('saved')
    expect(viewFromHash('#priced')).toBe('priced')
    expect(viewFromHash('#queue')).toBe('queue')
  })

  it('accepts a hash with no leading #', () => {
    expect(viewFromHash('items')).toBe('items')
  })

  it('rejects anything it does not recognise', () => {
    // Anyone can type anything after the #. An unvalidated value matches
    // neither panel and renders a blank page.
    expect(viewFromHash('#nonsense')).toBeNull()
    expect(viewFromHash('#Items')).toBeNull()      // case matters
    expect(viewFromHash('#items/2')).toBeNull()
  })

  it('treats an absent or empty hash as no view', () => {
    expect(viewFromHash('')).toBeNull()
    expect(viewFromHash('#')).toBeNull()
    expect(viewFromHash(undefined)).toBeNull()
    expect(viewFromHash(null)).toBeNull()
  })
})

describe('initialView', () => {
  it('opens on the tab named in the URL', () => {
    // The actual request: refresh on My inventory, come back to My
    // inventory - not to Auctions.
    expect(initialView('#items', storage())).toBe('items')
  })

  it('lets the URL win over the last-used tab', () => {
    // A shared link to #items must open there even though this browser
    // last had Auctions open, or the link does not mean anything.
    const s = storage({ [VIEW_KEY]: 'auctions' })
    expect(initialView('#items', s)).toBe('items')
  })

  it('falls back to the last-used tab for a bare URL', () => {
    const s = storage({ [VIEW_KEY]: 'items' })
    expect(initialView('', s)).toBe('items')
  })

  it('defaults to auctions with nothing to go on', () => {
    expect(initialView('', storage())).toBe(DEFAULT_VIEW)
  })

  it('ignores a junk value in storage', () => {
    // Storage outlives deploys, so a view name that no longer exists can
    // still be sitting in it after a rename.
    expect(initialView('', storage({ [VIEW_KEY]: 'dashboard' }))).toBe(DEFAULT_VIEW)
  })

  it('survives storage that throws', () => {
    expect(initialView('', hostile)).toBe(DEFAULT_VIEW)
  })

  it('survives storage being absent entirely', () => {
    expect(initialView('', undefined)).toBe(DEFAULT_VIEW)
  })

  it('still honours the URL when storage is broken', () => {
    // The hash is read before storage is touched, so a hostile browser
    // does not cost the feature.
    expect(initialView('#items', hostile)).toBe('items')
  })
})

describe('saveView', () => {
  it('records the tab for next time', () => {
    const s = storage()
    saveView('items', s)
    expect(s.data[VIEW_KEY]).toBe('items')
  })

  it('does not throw when storage does', () => {
    expect(() => saveView('items', hostile)).not.toThrow()
    expect(() => saveView('items', undefined)).not.toThrow()
  })
})

describe('viewUrl', () => {
  it('keeps the path and query and replaces the hash', () => {
    expect(viewUrl('items', { pathname: '/', search: '' })).toBe('/#items')
    expect(viewUrl('items', { pathname: '/app', search: '?debug=1' }))
      .toBe('/app?debug=1#items')
  })

  it('round-trips through viewFromHash', () => {
    // The two halves have to agree, or every render would see a mismatch
    // and push another history entry.
    for (const v of VIEWS) {
      const url = viewUrl(v, { pathname: '/', search: '' })
      expect(viewFromHash(url.slice(url.indexOf('#')))).toBe(v)
    }
  })
})
