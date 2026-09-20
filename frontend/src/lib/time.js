// Backend timestamps are naive UTC — append Z so the browser doesn't
// misread them as local time (that misread hid a 5-hour closing-time bug).
// Lives outside api.js so pure modules (and tests) can use it without
// dragging in the fetch layer, which touches `window` at import time.
export function parseUtc(s) {
  if (!s) return null
  return new Date(/Z$|[+-]\d\d:\d\d$/.test(s) ? s : s + 'Z')
}
