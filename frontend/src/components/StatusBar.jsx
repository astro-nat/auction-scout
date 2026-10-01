import { useEffect, useRef, useState } from 'react'
import { notify } from '../lib/notice'
import { cancelEnrichment, cancelJob, fetchStatus } from '../api'
import { etaLabel, sampleProgress } from '../lib/progress'

// Publish the bar's current height as a CSS variable so sticky table
// headers can offset themselves below it instead of hiding under it.
// Runs after every render; when the bar isn't rendered the ref is null
// and the offset collapses to 0px.
function useStatusBarHeightVar() {
  const ref = useRef(null)
  useEffect(() => {
    const h = ref.current ? ref.current.offsetHeight : 0
    document.documentElement.style.setProperty('--statusbar-h', `${h}px`)
    return () => document.documentElement.style.setProperty('--statusbar-h', '0px')
  })
  return ref
}

// Folded or open, remembered per browser. Storage can be missing or throw
// (private windows); a narrow window then starts folded, a wide one open.
const FOLD_KEY = 'auctionscout.statusbarFolded'
function initiallyFolded() {
  try {
    const saved = window.localStorage.getItem(FOLD_KEY)
    if (saved === 'true' || saved === 'false') return saved === 'true'
  } catch { /* fall through to the width */ }
  return typeof window !== 'undefined' && window.innerWidth < 768
}

// Fixed bar across the very top: what the server is doing right now, with
// real counts ("Importing 29 of 212"). Hidden entirely when nothing is
// running, so it never steals space from the app - and foldable to one
// line when something is, because two or three jobs with their progress
// bars took a real bite out of a small window.
export default function StatusBar({ onQuiet, onStatus }) {
  const [status, setStatus] = useState(null)
  const [folded, setFoldedState] = useState(initiallyFolded)
  const setFolded = (v) => {
    setFoldedState(v)
    try { window.localStorage.setItem(FOLD_KEY, String(v)) } catch { /* a preference */ }
  }
  const barRef = useStatusBarHeightVar()
  // Per-job progress samples (jobId → [{t, current}...]) so each row can
  // show a measured pace and time-remaining, same as the enrichment queue.
  const progressRef = useRef(new Map())
  // Read through a ref so a new callback never restarts the poll.
  const onStatusRef = useRef(onStatus)
  onStatusRef.current = onStatus


  useEffect(() => {
    let alive = true
    let wasBusy = false

    async function tick() {
      try {
        const s = await fetchStatus()
        if (!alive) return
        sampleProgress(progressRef.current, s.jobs || [])
        setStatus(s)
        onStatusRef.current?.(s)
        const busy = s.jobs.length > 0 || s.enrichment.queued > 0
        // Fire once on the busy → idle edge so the page can refresh itself.
        if (wasBusy && !busy) onQuiet?.()
        wasBusy = busy
      } catch {
        /* transient — keep polling */
      }
    }

    tick()
    // 1s: fast enough that per-lot stage changes and queue counts visibly
    // tick down. The endpoint is one aggregate query — cheap to poll.
    const interval = setInterval(tick, 1000)
    return () => { alive = false; clearInterval(interval) }
  }, [onQuiet])

  if (!status) return null
  const { jobs, enrichment } = status
  const lines = []

  for (const job of jobs) {
    // Counts first: on a phone the auction name is long and the tail gets
    // ellipsised, which is exactly where the numbers used to live.
    // detail = exactly what the job is touching right now (lot title,
    // auction name) — the counts alone made the bar feel vague.
    const detail = job.detail ? ` — ${job.detail}` : ''
    const text = (job.total
      ? `${job.current} of ${job.total} · ${job.label}`
      : job.label) + detail
    lines.push({
      key: job.id, text, current: job.current, total: job.total,
      eta: etaLabel(progressRef.current, job),
      onCancel: job.cancelled ? null : () => cancelJob(job.id),
    })
  }

  if (enrichment.queued > 0) {
    const stage = enrichment.stage ? ` — ${enrichment.stage}` : ''
    const lot = enrichment.lot_title ? ` (${enrichment.lot_title.slice(0, 40)})` : ''
    // Queue composition + throughput, straight from postgres: what kinds of
    // work are waiting and how fast the queue is actually moving.
    const parts = []
    if (enrichment.enrich_queued > 0) parts.push(`${enrichment.enrich_queued} enrich`)
    if (enrichment.inspect_queued > 0) parts.push(`${enrichment.inspect_queued} inspect`)
    const mix = parts.length > 1 ? ` (${parts.join(' · ')})` : ''
    const rate = enrichment.done_last_5min > 0
      ? ` · ${Math.round(enrichment.done_last_5min / 5)}/min · ~${Math.ceil(enrichment.queued / Math.max(1, enrichment.done_last_5min / 5))} min left`
      : ''
    lines.push({
      key: 'enrichment',
      // Same reasoning: the estimate rides beside the bar, not on the end of
      // a label that gets cut.
      eta: rate.replace(/^\s*·\s*/, ''),
      text: `Queue: ${enrichment.queued}${mix}${lot}${stage}`,
      onCancel: async () => {
        const r = await cancelEnrichment()
        notify(`Stopped ${r.cancelled} queued lots. The one in progress will finish.`)
      },
    })
  }

  // Nothing consuming the queue. This is the failure the process split
  // introduced: no request fails, nothing errors, work just sits at
  // 'pending' looking like it's about to start. Only worth saying when
  // there IS work — an idle app with no worker is not a problem yet.
  const noWorker = status.workers && status.workers.live === 0
  const pendingWork = jobs.length > 0 || enrichment.queued > 0
  if (noWorker && pendingWork) {
    lines.push({
      key: 'no-worker',
      warn: true,
      text: 'No worker process is running — queued work is not being picked up.',
    })
  }

  if (!lines.length) return null

  const barStyle = {
    position: 'sticky', top: 0, zIndex: 1000,
    background: 'var(--card-bg)', borderBottom: '1px solid var(--border)',
    padding: '6px 10px', fontSize: 13, boxShadow: 'var(--shadow)',
  }
  const work = lines.filter((l) => !l.warn)
  const warning = lines.find((l) => l.warn)

  if (folded) {
    // One line: how much is running, the first job's progress, and the
    // no-worker warning if there is one - that is a problem, not progress.
    const first = work.find((l) => l.total > 0)
    const pct = first ? Math.min(100, Math.round((first.current / first.total) * 100)) : null
    return (
      <div ref={barRef} role="status" aria-live="polite" style={{ ...barStyle, padding: '4px 10px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          {work.length > 0 && <span className="spinner" />}
          <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                         color: warning ? 'var(--warn)' : undefined,
                         fontWeight: warning ? 600 : undefined }}>
            {warning ? warning.text
              : `${work.length} ${work.length === 1 ? 'job' : 'jobs'} running`}
          </span>
          {pct !== null && !warning && (
            <strong style={{ flexShrink: 0, fontVariantNumeric: 'tabular-nums' }}>{pct}%</strong>
          )}
          <button type="button" onClick={() => setFolded(false)}
                  data-track="Show status bar" aria-expanded="false"
                  title="Show every running job, with its progress and a Cancel button"
                  style={{ flexShrink: 0, fontSize: 12, padding: '2px 8px' }}>
            Show ▾
          </button>
        </div>
      </div>
    )
  }

  return (
    <div ref={barRef} role="status" aria-live="polite" style={barStyle}>
      <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 2 }}>
        <button type="button" onClick={() => setFolded(true)}
                data-track="Hide status bar" aria-expanded="true"
                title="Fold this to one line - the jobs keep running"
                style={{ fontSize: 12, padding: '1px 8px' }}>
          Hide ▴
        </button>
      </div>
      {lines.map((l) => {
        const pct = l.total > 0
          ? Math.min(100, Math.round((l.current / l.total) * 100))
          : null
        return (
          <div key={l.key} style={{ marginBottom: lines.length > 1 ? 6 : 0 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              {/* A warning is not progress — no spinner, and it says so. */}
              {l.warn ? <span aria-hidden="true">!</span> : <span className="spinner" />}
              <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis',
                             whiteSpace: 'nowrap',
                             color: l.warn ? 'var(--warn)' : undefined,
                             fontWeight: l.warn ? 600 : undefined }}>
                {l.text}
              </span>
              {/* Its own slot, like the percentage: never ellipsised, however
                  long the auction name is. */}
              {l.eta && (
                <span style={{ flexShrink: 0, whiteSpace: 'nowrap',
                               color: 'var(--muted)' }}>{l.eta}</span>
              )}
              {pct !== null && (
                <strong style={{ flexShrink: 0, fontVariantNumeric: 'tabular-nums' }}>{pct}%</strong>
              )}
              {l.onCancel && (
                <button onClick={l.onCancel} title="Stop this — work already done is kept"
                        aria-label={`Cancel: ${l.text}`} data-track="Cancel job (status bar)"
                        style={{ flexShrink: 0, fontSize: 12, padding: '2px 8px' }}>
                  Cancel
                </button>
              )}
            </div>
            {pct !== null && (
              // Full-width track under the text — a 90px sliver at the far
              // right was easy to miss, especially on a phone.
              <div role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}
                   aria-label={l.text} style={{
                height: 8, borderRadius: 4, background: 'var(--badge-bg)',
                overflow: 'hidden', marginTop: 4,
              }}>
                <div style={{
                  height: '100%', width: `${pct}%`, background: 'var(--accent)',
                  transition: 'width 0.4s ease',
                }} />
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}
