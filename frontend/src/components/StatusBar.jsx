import { useEffect, useState } from 'react'
import { fetchStatus } from '../api'

// Fixed bar across the very top: what the server is doing right now, with
// real counts ("Importing 29 of 212"). Hidden entirely when nothing is
// running, so it never steals space from the app.
export default function StatusBar({ onQuiet }) {
  const [status, setStatus] = useState(null)

  useEffect(() => {
    let alive = true
    let wasBusy = false

    async function tick() {
      try {
        const s = await fetchStatus()
        if (!alive) return
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
    const interval = setInterval(tick, 2000)
    return () => { alive = false; clearInterval(interval) }
  }, [onQuiet])

  if (!status) return null
  const { jobs, enrichment } = status
  const lines = []

  for (const job of jobs) {
    // Counts first: on a phone the auction name is long and the tail gets
    // ellipsised, which is exactly where the numbers used to live.
    const text = job.total
      ? `${job.current} of ${job.total} · ${job.label}`
      : job.label
    lines.push({ key: job.id, text, current: job.current, total: job.total })
  }

  if (enrichment.queued > 0) {
    const stage = enrichment.stage ? ` — ${enrichment.stage}` : ''
    const lot = enrichment.lot_title ? ` (${enrichment.lot_title.slice(0, 40)})` : ''
    lines.push({
      key: 'enrichment',
      text: `Enriching ${enrichment.queued} lot${enrichment.queued === 1 ? '' : 's'}${lot}${stage}`,
    })
  }

  if (!lines.length) return null

  return (
    <div style={{
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
