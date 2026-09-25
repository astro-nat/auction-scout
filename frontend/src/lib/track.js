// Usage tracking for the UI: which buttons, filters, sorts and views get
// used. Recorded to the app's own backend, never a third party. The core is
// a pure queue so the batching rules are tested without a DOM; the DOM
// hooks at the bottom are the only part that touches the page.

import { API_BASE, postEvents } from '../api'
import { viewFromHash } from './view'

const NAME_MAX = 60

// "Price all 386 with AI" and "Price all 12 with AI" are the same button.
// Digits collapse to N so counts aggregate; whitespace collapses; long
// labels are cut so a props blob stays small.
export function normalizeLabel(text) {
  return String(text || '')
    .replace(/\s+/g, ' ')
    .trim()
    .replace(/\d[\d,.]*/g, 'N')
    .slice(0, 40)
}

// The queue. Events pile up and go out together - after `flushEvery` ms,
// or sooner when `maxBatch` are waiting - so a click never waits on the
// network. `send` must never surface a failure to the caller: losing a
// usage event is not worth an error in front of the user.
export function makeTracker({
  send,
  flushEvery = 3000,
  maxBatch = 20,
  view = () => null,
  schedule = (fn, ms) => setTimeout(fn, ms),
  cancel = (t) => clearTimeout(t),
  enabled = true,
} = {}) {
  let queue = []
  let timer = null

  // Take everything waiting, without sending it. flush() sends what drain()
  // returns; the page-unload path sends it by beacon instead, because a
  // fetch started during unload may never leave the tab.
  function drain() {
    if (timer != null) { cancel(timer); timer = null }
    const batch = queue
    queue = []
    return batch
  }

  function flush() {
    const batch = drain()
    if (!batch.length) return batch
    try {
      const r = send(batch)
      if (r && typeof r.catch === 'function') r.catch(() => {})
    } catch {
      // never surface
    }
    return batch
  }

  function track(name, props) {
    if (!enabled || !name) return
    let v = null
    try { v = view() } catch { v = null }
    queue.push({ name: String(name).slice(0, NAME_MAX), props: props ?? null, view: v })
    if (queue.length >= maxBatch) flush()
    else if (timer == null) timer = schedule(flush, flushEvery)
  }

  return { track, flush, drain, pending: () => queue.length }
}

// What a clicked button is called in the log. Tab and view-switch buttons
// return null: the switch is already logged as a 'view' event, and counting
// the click as well doubled every one. A button can name itself with
// data-track when its label is shared with a different button.
export function buttonName(b) {
  if (!b) return null
  const cls = b.classList
  if (cls && (cls.contains('tab') || cls.contains('subtab'))) return null
  return normalizeLabel(b.dataset?.track || b.innerText || b.title || 'button')
}

// What a checkbox is called in the log: its own name if it has one, else
// the text of the label around it. Most filter boxes carry no title, so
// every one of them used to log as plain "checkbox".
export function checkboxName(el) {
  const own = el.dataset?.track || el.getAttribute?.('aria-label') || el.title
  if (own) return normalizeLabel(own)
  const text = el.closest?.('label')?.innerText?.trim()
  return normalizeLabel(text || 'checkbox')
}

// --- the page ---------------------------------------------------------

const tracker = makeTracker({
  send: (batch) => postEvents(batch),
  view: () => (typeof window === 'undefined' ? null : (viewFromHash(window.location.hash) || 'auctions')),
})

export const track = tracker.track

// Every button on the page, counted by its label, without touching each
// handler: one delegated listener. Checkboxes count by their title (the
// selection boxes carry one). Returns the uninstaller.
export function installTracking(root = document) {
  const onClick = (ev) => {
    const b = ev.target && ev.target.closest && ev.target.closest('button')
    const name = buttonName(b)
    if (name) track('button', { name })
  }
  const onChange = (ev) => {
    const el = ev.target
    if (!el || el.type !== 'checkbox') return
    track('check', { name: checkboxName(el), on: !!el.checked })
  }
  // The last few seconds of activity when the tab closes or goes to the
  // background: a beacon survives page unload where fetch may not.
  const onHide = () => {
    if (document.visibilityState !== 'hidden') return
    const events = tracker.drain()
    if (!events.length) return
    try {
      const sent = navigator.sendBeacon?.(`${API_BASE}/events`,
        new Blob([JSON.stringify({ events })], { type: 'application/json' }))
      if (!sent) postEvents(events).catch(() => {})
    } catch { /* never surface */ }
  }
  root.addEventListener('click', onClick, true)
  root.addEventListener('change', onChange, true)
  document.addEventListener('visibilitychange', onHide)
  return () => {
    root.removeEventListener('click', onClick, true)
    root.removeEventListener('change', onChange, true)
    document.removeEventListener('visibilitychange', onHide)
  }
}
