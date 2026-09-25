// Pages, page buttons and the one-box search for the inventory table, in
// the DataTables mould ("Show N per page", "Showing 51 to 100 of 386",
// « ‹ 1 … 4 5 6 … 20 › »). Pure so it's testable.

export const PAGE_SIZES = [25, 50, 100, 250]
export const DEFAULT_PAGE_SIZE = 50
const SIZE_KEY = 'auctionscout.pageSize'

// Which slice of `total` rows page `page` shows. The page is clamped, so a
// filter that shrinks the result never strands the view past its end.
export function pageWindow(total, page, size) {
  const pages = Math.max(1, Math.ceil(total / size))
  const p = Math.min(Math.max(1, page), pages)
  const start = (p - 1) * size
  return { page: p, pages, start, end: Math.min(total, start + size) }
}

// Numbered buttons: always the first and last page, `width` either side
// of the current one, and an ellipsis for each gap.
export function pageButtons(page, pages, width = 1) {
  const keep = new Set([1, pages])
  for (let i = page - width; i <= page + width; i++) if (i >= 1 && i <= pages) keep.add(i)
  const out = []
  let prev = 0
  for (const k of [...keep].sort((a, b) => a - b)) {
    if (k - prev === 2) out.push(prev + 1)          // a gap of one: show the page
    else if (k - prev > 2) out.push('…')
    out.push(k)
    prev = k
  }
  return out
}

// "Showing 51 to 100 of 386 items"
export function showingText(total, start, end) {
  if (!total) return 'No items match'
  return `Showing ${(start + 1).toLocaleString()} to ${end.toLocaleString()} of `
    + `${total.toLocaleString()} item${total === 1 ? '' : 's'}`
}

// The search box: every word must appear somewhere in the lot's title, AI
// title, auction, category or lot number, in any order, any case.
export function searchMatches(lot, query) {
  const words = String(query || '').toLowerCase().split(/\s+/).filter(Boolean)
  if (!words.length) return true
  const hay = [lot.lot_number, lot.title, lot.enrichment?.enriched_title,
               lot.auction_name, lot.category].filter(Boolean).join(' ').toLowerCase()
  return words.every((w) => hay.includes(w))
}

// The page size is a per-browser preference. Storage can throw outright in
// some privacy modes, so both directions are guarded.
export function savedPageSize(storage) {
  try {
    const n = Number(storage?.getItem(SIZE_KEY))
    return PAGE_SIZES.includes(n) ? n : DEFAULT_PAGE_SIZE
  } catch {
    return DEFAULT_PAGE_SIZE
  }
}

export function savePageSize(size, storage) {
  try { storage?.setItem(SIZE_KEY, String(size)) } catch { /* a preference, not worth an error */ }
}
