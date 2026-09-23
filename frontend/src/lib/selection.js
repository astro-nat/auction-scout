// Multi-select over the lot table. Pure, so the rules can be tested
// without rendering: what "select all" covers, what an action acts on when
// the filters have changed since the ticks were made, and how per-lot
// endpoints are fanned out without hammering the API.

// A new Set with the id toggled; never mutates the one React holds.
export function toggle(selected, id) {
  const next = new Set(selected)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  return next
}

// "Select all" covers every lot matching the current filters - the whole
// result, not just the rows the render cap has painted. The cap exists to
// save DOM memory; it must not silently shrink a bulk action.
export function selectAll(lots) {
  return new Set(lots.map((l) => l.lot_id))
}

// What an action acts on: the ticked lots that are STILL in the current
// result. Ticks made before a filter change survive, but a lot the filter
// now hides is not acted on - the user can no longer see what they would
// be doing to it.
export function inView(selected, lots) {
  return lots.filter((l) => selected.has(l.lot_id))
}

export function allSelected(selected, lots) {
  return lots.length > 0 && lots.every((l) => selected.has(l.lot_id))
}

// Per-lot endpoints (hide, watch) a few at a time: sequential is slow on a
// hundred lots, all-at-once is a request storm.
export function chunked(items, size) {
  const out = []
  for (let i = 0; i < items.length; i += size) out.push(items.slice(i, i + size))
  return out
}
