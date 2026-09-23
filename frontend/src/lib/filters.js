// Column filters for the lot table.
//
// A query is either a comparison ('>50', '<10') or a substring. Numbers
// compare numerically; anything else is a case-insensitive contains. Pure,
// so the direction and the units can be tested without rendering a table.

// Money columns: "under N dollars" is the natural question for a bid or a
// cost - you are looking for cheap entries.
export const MONEY_RANGES = ['<5', '<10', '<25', '<50', '<100']

// ROI is the opposite question - "at least N percent" - and it is in
// PERCENT, matching what the cell displays. The column used to hand this
// dropdown the money presets: every option was "less than", and because the
// stored value is a ratio (0.76, shown as 76%), "<5" meant "under 500%" and
// matched everything.
export const ROI_RANGES = ['>0', '>50', '>100', '>150', '>200']

export function matchesFilter(value, query) {
  if (!query) return true
  if (value == null) return false
  if (query.startsWith('>')) return typeof value === 'number' && value > parseFloat(query.slice(1))
  if (query.startsWith('<')) return typeof value === 'number' && value < parseFloat(query.slice(1))
  if (typeof value === 'number') return String(value).includes(query)
  return String(value).toLowerCase().includes(query.toLowerCase())
}

// The ROI column's value in the units the filter and the cell agree on.
export function roiPercent(estRoi) {
  if (estRoi === null || estRoi === undefined) return null
  return Math.round(Number(estRoi) * 100)
}
