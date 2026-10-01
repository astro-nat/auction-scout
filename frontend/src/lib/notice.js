// One-line notices that don't block the page. "Queued 40 items" used to be
// an alert() you had to dismiss before doing anything else - and most of
// them only confirm what the status bar is already showing.
//
// A notice may carry one action: { label, onClick } - a button beside the
// text, for "you're doing this one at a time; do the rest?" moments.
const listeners = new Set()

export function notify(text, action = null) {
  for (const fn of listeners) fn({ text: String(text), action })
}

export function onNotice(fn) {
  listeners.add(fn)
  return () => listeners.delete(fn)
}
