import { useEffect, useRef, useState } from 'react'
import { cancelEnrichment, cancelJob, fetchStatus } from '../api'

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

// Fixed bar across the very top: what the server is doing right now, with
// real counts ("Importing 29 of 212"). Hidden entirely when nothing is
// running, so it never steals space from the app.
export default function StatusBar({ onQuiet }) {
  const [status, setStatus] = useState(null)
  const barRef = useStatusBarHeightVar()
  // Per-job progress samples (jobId → [{t, current}...]) so each row can
  // show a measured pace and time-remaining, same as the enrichment queue.
  const progressRef = useRef(new Map())

  function sampleProgress(jobs) {
    const now = Date.now()
    const map = progressRef.current
    const seen = new Set()
    for (const j of jobs) {
      if (j.total == null) continue
      seen.add(j.id)
      const arr = map.get(j.id) ?? []
      arr.push({ t: now, current: j.current || 0 })
      while (arr.length > 20) arr.shift()
      map.set(j.id, arr)
    }
    for (const id of [...map.keys()]) if (!seen.has(id)) map.delete(id)
  }

  function etaText(job) {
    const arr = progressRef.current.get(job.id)
    if (!arr || arr.length < 3 || job.total == null) return ''
    const first = arr[0], last = arr[arr.length - 1]
    const dt = (last.t - first.t) / 1000
    const done = last.current - first.current
    if (dt < 5 || done <= 0) return ''
    const perSec = done / dt
    const remaining = (job.total - last.current) / perSec
    if (remaining < 90) return ` · ~${Math.max(1, Math.round(remaining / 10) * 10)}s left`
    return ` · ~${Math.ceil(remaining / 60)} min left`
  }

  useEffect(() => {
    let alive = true
    let wasBusy = false

    async function tick() {
      try {
        const s = await fetchStatus()
        if (!alive) return
        sampleProgress(s.jobs || [])
        setStatus(s)
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
      ? `${job.current} of ${job.total} · ${job.label}${etaText(job)}`
      : job.label) + detail
    lines.push({
      key: job.id, text, current: job.current, total: job.total,
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
      text: `Queue: ${enrichment.queued}${mix}${rate}${lot}${stage}`,
      onCancel: async () => {
        const r = await cancelEnrichment()
        alert(`Stopped ${r.cancelled} queued lots. The one in progress will finish.`)
      },
    })
  }

  if (!lines.length) return null

  return (
    <div ref={barRef} style={{
      position: 'sticky', top: 0, zIndex: 1000,
      background: 'var(--card-bg)', borderBottom: '1px solid var(--border)',
      padding: '6px 10px', fontSize: 13, boxShadow: '0 1px 4px rgba(0,0,0,0.25)',
    }}>
      {lines.map((l) => {
        const pct = l.total > 0
          ? Math.min(100, Math.round((l.current / l.total) * 100))
          : null
        return (
          <div key={l.key} style={{ marginBottom: lines.length > 1 ? 6 : 0 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <span className="spinner" />
              <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {l.text}
              </span>
              {pct !== null && (
                <strong style={{ flexShrink: 0, fontVariantNumeric: 'tabular-nums' }}>{pct}%</strong>
              )}
              {l.onCancel && (
                <button onClick={l.onCancel} title="Stop this — work already done is kept"
                        style={{ flexShrink: 0, fontSize: 12, padding: '2px 8px' }}>
                  Cancel
                </button>
              )}
            </div>
            {pct !== null && (
              // Full-width track under the text — a 90px sliver at the far
              // right was easy to miss, especially on a phone.
              <div style={{
                height: 8, borderRadius: 4, background: 'var(--badge-bg)',
                overflow: 'hidden', marginTop: 4,
              }}>
                <div style={{
                  height: '100%', width: `${pct}%`, background: 'var(--link)',
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
