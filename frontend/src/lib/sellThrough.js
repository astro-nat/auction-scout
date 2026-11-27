// Sell-through rate, checked on gold mines only (backend
// services/sell_through): the lot's own relevant sold comps from the last
// 90 days against eBay's matching active listings. Shown, not graded on.
//
// Only readings from the current method are shown. The two before it
// compared different things and read 0% or 92% for nearly everything
// (2026-11-25); the sweep redoes them, and until it does they stay hidden.
export const STR_METHOD = 'matched-v3'

export function strLabel(e) {
  if (e?.str_pct == null || e.str_method !== STR_METHOD) return null
  return `STR ${Math.round(Number(e.str_pct))}%`
}

export function strTitle(e) {
  if (strLabel(e) == null) return undefined
  const n = (v) => Number(v ?? 0).toLocaleString()
  const when = e.str_checked_at
    ? `, checked ${new Date(`${e.str_checked_at}Z`).toLocaleDateString()}`
    : ''
  return `Sell-through: ${n(e.str_sold)} sold in the last 90 days vs ${n(e.str_active)} listed now on eBay, counting only listings of the same item${when}.`
}
