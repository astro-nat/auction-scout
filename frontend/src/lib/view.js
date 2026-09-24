// Which tab the app opens on.
//
// The URL is the source of truth (#items), so a refresh stays put, links
// are shareable, and back/forward move between tabs for free. localStorage
// is only the fallback for a bare URL with no hash - a bookmark to the root
// still returns you to the tab you last used.
//
// Pure functions taking the hash and the storage object, rather than
// reaching for window themselves, so they can be tested without a DOM.

export const VIEW_KEY = 'auctionscout.view'
// Find auctions, the auctions you have imported from, every imported lot,
// just the lots the app has put a value on, and the work still to do.
export const VIEWS = ['auctions', 'saved', 'items', 'priced', 'queue']
export const DEFAULT_VIEW = 'auctions'

// Anyone can type anything after the #, and an unrecognised value would
// match neither panel and render a blank page. Returns null for "no usable
// view here", which is distinct from a valid one.
export function viewFromHash(hash) {
  const raw = (hash || '').replace(/^#/, '')
  return VIEWS.includes(raw) ? raw : null
}

// Storage access is guarded because it THROWS outright in some privacy
// modes rather than returning null - an unguarded read takes down the
// first render.
export function initialView(hash, storage) {
  const fromHash = viewFromHash(hash)
  if (fromHash) return fromHash
  try {
    const saved = storage?.getItem(VIEW_KEY)
    return VIEWS.includes(saved) ? saved : DEFAULT_VIEW
  } catch {
    return DEFAULT_VIEW
  }
}

export function saveView(view, storage) {
  try {
    storage?.setItem(VIEW_KEY, view)
  } catch {
    // A preference that cannot be saved is not worth breaking a render.
  }
}

// The URL to put in the address bar for a view, keeping path and query.
export function viewUrl(view, location) {
  return `${location.pathname}${location.search}#${view}`
}
