// The homework behind a resale number. The comp records themselves come
// from the backend (enrichment.comps); these helpers build the one-tap
// verification links and labels the panel renders. Pure, so testable.

// eBay's completed+sold filter — the search the user was running by hand
// (Google, then eBay, then asking another AI) every time a number looked
// wrong. Sold listings only: asking prices are hopes, not evidence.
export function ebaySoldUrl(title) {
  if (!title || !title.trim()) return null
  return 'https://www.ebay.com/sch/i.html?_nkw='
    + encodeURIComponent(title.trim())
    + '&LH_Sold=1&LH_Complete=1'
}

// One comp's provenance, in a word the panel can print.
export function kindLabel(kind) {
  if (kind === 'asking') return 'asking'
  if (kind === 'retail') return 'retail'
  return 'sold'
}

// Render-ready evidence rows, so the panel component is a dumb map and the
// formatting (price, provenance, date truncation, link fallback) is
// testable without a DOM.
export function compRows(comps) {
  return (comps || []).map((c) => ({
    label: `$${Number(c.price).toFixed(2)} ${kindLabel(c.kind)}`
      + (c.date ? ` ${String(c.date).slice(0, 10)}` : ''),
    title: c.title || '',
    url: c.url || null,
  }))
}
