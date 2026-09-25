// How many of an auction's lots match the scan filters on screen right
// now, and what the Import button should say about it.
//
// The count comes from the scan (HiBid's own lot search, category and/or
// keyword together) and is only trustworthy when it was computed under
// the EXACT filter combination active right now — a count left over from
// a different category or a cleared keyword would silently mislabel the
// button, or worse, make "Import all" skip an auction that only looked
// empty under a filter that no longer applies.

export function matchCountFor(auction, categoryId, searchText) {
  const wantsCategory = categoryId != null && categoryId !== -1
  const query = (searchText || '').trim() || null
  if (!wantsCategory && !query) return null
  if (auction.category_lot_count == null) return null
  const countedCategory = auction.category_count_for ?? -1
  if (countedCategory !== (wantsCategory ? categoryId : -1)) return null
  if ((auction.category_count_search || null) !== query) return null
  return auction.category_lot_count
}

// The per-row Import button's label. Mirrors what the import will
// actually pull: HiBid's own lot search applies category and keyword
// together, server-side, so one count covers whichever are active.
export function importLabel(auction, { categoryId, categoryName, searchText, mobile } = {}) {
  const count = matchCountFor(auction, categoryId, searchText)
  if (count == null) return 'Import'
  if (mobile) return `Import ${count}`
  const query = (searchText || '').trim()
  const parts = []
  if (categoryId != null && categoryId !== -1) parts.push(categoryName || 'matching')
  if (query) parts.push(`"${query}"`)
  return parts.length ? `Import ${count} ${parts.join(' · ')}` : `Import ${count}`
}
