// Where a lot stands on the comps-first, AI-second path, and what its one
// row button offers next. Pure so it's testable.

// AI has already priced it. Locked: nothing runs AI or comps on it again.
// A 'success' the AI never actually saw (it was unreachable) doesn't count.
export function aiDone(e) {
  return !!e && e.status === 'success' && !!e.ai_source && e.ai_source !== 'none'
}

// The row's single button: comps first, AI second, then locked.
export function rowAction(e, working) {
  if (working || e?.status === 'queued') {
    return { step: 'working', label: 'Working…', disabled: true }
  }
  if (aiDone(e)) {
    return { step: 'locked', label: 'AI checked', disabled: true,
             title: 'Priced by AI. Locked so nothing runs AI or a comp search on it again.' }
  }
  if (e?.est_resale == null) {
    return { step: 'comps', label: 'Price with comps', disabled: false,
             title: 'Look up sold comps on this lot\'s own title. No AI cost.' }
  }
  return { step: 'ai', label: 'Further inspect with AI', disabled: false,
           title: 'AI reads the photo and listing for condition and a closer identification, '
                  + 'then prices it again. A box of many items is priced item by item. '
                  + 'After this the lot is locked.' }
}
