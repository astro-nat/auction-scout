// Progress sampling and time-remaining for the status bar.
//
// Pulled out of the component so it can be exercised directly: the bug this
// fixes was invisible in the UI — the ETA didn't render wrong, it silently
// didn't render at all — and there was no way to check the arithmetic.

const MAX_SAMPLES = 20

// Below this the rate is too noisy to extrapolate from.
const MIN_WINDOW_SECONDS = 5
const MIN_SAMPLES = 3

/**
 * Record where each job is right now.
 *
 * `map` is jobId -> [{t, current, total}]. Jobs that have gone away are
 * dropped, so a finished job's samples can't seed the next one that happens
 * to reuse its id.
 */
export function sampleProgress(map, jobs, now = Date.now()) {
  const seen = new Set()
  for (const j of jobs) {
    if (j.total == null) continue
    seen.add(j.id)
    let arr = map.get(j.id) ?? []
    const last = arr[arr.length - 1]
    // Import runs two phases under ONE job id: it counts up while fetching
    // from HiBid, then resets to 0 with a new total while saving. Kept in
    // one series, elapsed progress goes negative across that boundary and
    // the ETA disappears for the whole save -- the slower half. Start a
    // fresh series when the count jumps backwards or the total moves.
    if (last && (j.current < last.current || j.total !== last.total)) arr = []
    arr.push({ t: now, current: j.current || 0, total: j.total })
    while (arr.length > MAX_SAMPLES) arr.shift()
    map.set(j.id, arr)
  }
  for (const id of [...map.keys()]) if (!seen.has(id)) map.delete(id)
  return map
}

/** " - ~4 min left", or '' when there isn't enough signal to say. */
export function etaText(map, job) {
  const arr = map.get(job.id)
  if (!arr || arr.length < MIN_SAMPLES || job.total == null) return ''
  const first = arr[0], last = arr[arr.length - 1]
  const dt = (last.t - first.t) / 1000
  const done = last.current - first.current
  if (dt < MIN_WINDOW_SECONDS || done <= 0) return ''
  const perSec = done / dt
  const remaining = (job.total - last.current) / perSec
  if (remaining <= 0) return ''
  if (remaining < 90) return ` · ~${Math.max(1, Math.round(remaining / 10) * 10)}s left`
  return ` · ~${Math.ceil(remaining / 60)} min left`
}
