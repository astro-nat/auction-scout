// Shipping weight. "~" marks the rough item-type guess; a weight the AI
// read off the photos goes without it. Under a pound reads in ounces.
export function weightLabel(e) {
  if (e?.weight_lb == null) return null
  const lb = Number(e.weight_lb)
  const amount = lb < 1 ? `${Math.max(1, Math.round(lb * 16))} oz` : `${lb < 10 ? lb.toFixed(1).replace(/\.0$/, '') : Math.round(lb)} lb`
  return e.weight_source === 'ai' ? amount : `~${amount}`
}
export function weightTitle(e) {
  return e?.weight_source === 'ai'
    ? 'Shipping weight, estimated by AI from the photos'
    : 'Shipping weight, a rough guess from the item type'
}
