// Whether to offer "the rest like this one" after a hide, and what to say.
//
// Kept out of the component because the rule is the interesting part and a
// component in this app cannot be rendered in a test. What is at stake:
// offering nothing when there IS a pile wastes the one moment the user has
// just proved what they think of that product, and offering when there is
// no pile - or after they have said no - turns a helpful prompt into a nag.

// The peek is the server's dry-run hide-like: {key, matched, changed, titles}.
// key is null when the title is too generic to group on ("Pyrex" is a
// category, not a product), and changed counts only the lots that would
// actually move - the one just hidden is already out of it.
export function offerFrom(peek, dismissedKeys = new Set()) {
  if (!peek || !peek.key) return null
  if (!(peek.changed > 0)) return null
  if (dismissedKeys.has(peek.key)) return null
  return { key: peek.key, count: peek.changed, titles: peek.titles || [] }
}

// "3 more like that one" / "1 more like that one" - the count leads, because
// it is the whole reason to read the line.
export function offerLabel(offer) {
  if (!offer) return ''
  return `${offer.count.toLocaleString()} more like that one`
}
