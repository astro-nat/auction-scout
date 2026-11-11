// Sell-through rate, checked on gold mines only (backend
// services/sell_through): eBay sold / (sold + active) for the lot's search.
// Shown, not graded on.
export function strLabel(e) {
  if (e?.str_pct == null) return null
  return `STR ${Math.round(Number(e.str_pct))}%`
}

export function strTitle(e) {
  if (e?.str_pct == null) return undefined
  const n = (v) => Number(v ?? 0).toLocaleString()
  const when = e.str_checked_at
    ? `, checked ${new Date(`${e.str_checked_at}Z`).toLocaleDateString()}`
    : ''
  return `Sell-through: ${n(e.str_sold)} sold vs ${n(e.str_active)} listed on eBay${when}. eBay rounds big totals, so broad searches are approximate.`
}
