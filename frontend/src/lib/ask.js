// Confirmations as an in-page dialog instead of window.confirm: styled with
// the app, readable on a phone, and able to name the action on its button
// ("Delete 120 items") rather than a bare OK. Resolves true or false.
let open = null

export function ask(message, { confirmLabel = 'Continue', danger = false } = {}) {
  return new Promise((resolve) => {
    if (!open) { resolve(window.confirm(message)); return }   // not mounted (tests)
    open({ message: String(message), confirmLabel, danger, resolve })
  })
}

export function registerAsk(fn) {
  open = fn
  return () => { if (open === fn) open = null }
}
