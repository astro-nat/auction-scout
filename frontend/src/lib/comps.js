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
