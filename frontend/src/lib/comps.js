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
      + shipLabel(c)
      + (c.date ? ` ${String(c.date).slice(0, 10)}` : ''),
    title: c.title || '',
    url: c.url || null,
  }))
}

// How a sold comp shipped. The price is the item alone; on a free-shipping
// sale the seller paid the postage out of it, so it netted less than it
// reads - and you sell with the buyer paying postage. Comps priced before
// shipping was recorded carry nothing, and say nothing.
export function shipLabel(c) {
  if (c?.ship === 'free') return ' free ship'
  if (c?.ship === 'paid') return c.ship_cost != null ? ` +$${Number(c.ship_cost).toFixed(2)} ship` : ' + ship'
  if (c?.ship === 'pickup') return ' pickup'
  return ''
}

// "12 of 30 sold with free shipping" - or null when no comp says how it
// shipped (priced before it was recorded).
export function shippingSummary(comps) {
  const known = (comps || []).filter((c) => c?.ship)
  if (!known.length) return null
  const free = known.filter((c) => c.ship === 'free').length
  const unknown = (comps || []).length - known.length
  return `${free} of ${known.length} sold with free shipping`
    + (unknown ? ` (${unknown} not stated)` : '')
}
