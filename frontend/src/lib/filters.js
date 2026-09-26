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

// Closing-time presets. The value is what the filter compares - hours
// until close - and the label is what the dropdown shows, because "<48"
// reads as nonsense where "< 2 days" reads as a question. A preset may be
// a plain string (money) or a {value, label} pair; optionOf normalises.
export const CLOSING_RANGES = [
  { value: '<1', label: '< 1 hr' },
  { value: '<24', label: '< 24 hrs' },
  { value: '<48', label: '< 2 days' },
  { value: '<168', label: '< 7 days' },
  { value: '>168', label: '> 7 days' },
]

export function optionOf(preset) {
  return typeof preset === 'string' ? { value: preset, label: preset } : preset
}

// The options a column's filter offers, as {value, label} pairs. One rule
// for the desktop header and the phone panel, so the phone can generate
// its filters from the column list instead of hand-maintaining a subset
// that drifted: for months it had no way to filter by bid, cost, resale,
// max bid, ROI or auction at all.
export function presetsFor(col, distinctValues = {}) {
  if (col.filter === 'range') return (col.ranges ?? MONEY_RANGES).map(optionOf)
  if (col.filter === 'values') return (distinctValues[col.key] ?? []).map(optionOf)
  return []
}

// Hours from now until a lot closes, or null when there is nothing to
// count down to: no close time on file, or already closed. Null matches no
// preset, so "< 1 hr" is lots closing within the hour, not lots that
// closed an hour ago - those are not something you can still bid on.
export function hoursUntil(closesAt, now, parse = (s) => new Date(s)) {
  if (!closesAt) return null
  const ms = parse(closesAt).getTime() - now
  if (!Number.isFinite(ms) || ms <= 0) return null
  return ms / 3600000
}

// Is this lot a gold mine - its bid still clears the target return after
// fees? The grade is computed server-side and stored, so this only reads it.
//
// It lives here because "Gold mines only" stopped being a server filter. It
// was one, so every toggle refetched every page; the usage log shows it
// turned on and off six times in 44 hours, which is a comparison being made,
// not a filter being set. The lots are already in the browser and already
// carry the grade, so the answer is instant and the count can be shown
// beside the box - which is what the toggling was asking for.
export function isGoldMine(lot) {
  return (lot?.enrichment?.roi_status || '') === 'GOLD MINE'
}
