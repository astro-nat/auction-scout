// How much to trust an auction house's estimates, in one phrase.
//
// ratio = median(estimate_low / hammer) over the house's observed closed
// sales. Above 1 means estimates promise more than lots fetch. Below the
// observation floor nothing is shown at all: a ratio from a handful of
// sales is an anecdote wearing a number.
export const MIN_OBS = 20

export function houseRatioLabel(ratio, n) {
  if (ratio == null || !n || n < MIN_OBS) return null
  if (ratio >= 1.2) return `estimates ~${ratio.toFixed(1)}x hot`
  if (ratio < 0.8) return 'estimates run low'
  return 'estimates track results'
}

// The fuller sentence for tooltips, so the short label has its receipts.
export function houseRatioTitle(ratio, n) {
  if (ratio == null || !n || n < MIN_OBS) return null
  return `Across ${n} observed sales from this house, low estimates ran `
    + `~${ratio.toFixed(1)}x the realized hammer price. Discount its `
    + `estimates accordingly.`
}
