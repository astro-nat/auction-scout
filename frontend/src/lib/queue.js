// Wording for the Queue view, pure so it's testable.

// "12 of 40 done · 28 left" — or just the label's own words when the job
// has no count (a scan doesn't know its size up front).
export function jobProgress(job) {
  if (job.total == null) return job.state === 'pending' ? 'waiting to start' : 'working'
  const left = job.remaining ?? Math.max(0, job.total - (job.current || 0))
  if (job.state === 'pending') return `waiting to start · ${left} to do`
  return `${job.current || 0} of ${job.total} done · ${left} left`
}

// What a queued lot is waiting for, in plain words.
export function taskLabel(task) {
  return task === 'inspect' ? 'read the photo and price it' : 'price it'
}

// The number on the Queue pill: jobs still open plus lots still waiting.
export function queueCount(status) {
  if (!status) return 0
  return (status.jobs?.length || 0) + (status.enrichment?.queued || 0)
}
