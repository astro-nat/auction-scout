// One-line notices that don't block the page. "Queued 40 items" used to be
// an alert() you had to dismiss before doing anything else - and most of
// them only confirm what the status bar is already showing.
const listeners = new Set()

export function notify(text) {
  for (const fn of listeners) fn(String(text))
}

export function onNotice(fn) {
  listeners.add(fn)
  return () => listeners.delete(fn)
}
