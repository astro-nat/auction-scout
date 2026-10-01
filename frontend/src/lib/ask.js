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

// The same dialog with a text field: resolves the text typed (possibly
// empty) on confirm, or null on cancel.
export function askText(message, { confirmLabel = 'Continue', placeholder = '' } = {}) {
  return new Promise((resolve) => {
    if (!open) { resolve(window.prompt(message)); return }
    open({ message: String(message), confirmLabel, danger: false, input: true, placeholder,
           resolve: (ok, text) => resolve(ok ? (text ?? '') : null) })
  })
}

export function registerAsk(fn) {
  open = fn
  return () => { if (open === fn) open = null }
}
