import { useEffect, useState } from 'react'
import { fetchQueue, cancelJob, cancelEnrichment, moveQueuedJob, moveQueuedLot, moveJobItem, alertOnce } from '../api'
import { jobProgress, taskLabel } from '../lib/queue'

// Everything started and not yet finished: each background job with how
// far it has got and what it touches next, then every lot waiting to be
// priced in the order the worker will take them. Polls while open.
export default function QueueView({ isMobile }) {
  const [queue, setQueue] = useState(null)
  const [failed, setFailed] = useState(false)
  const [moving, setMoving] = useState(false)

  const load = async () => {
    try {
      setQueue(await fetchQueue())
      setFailed(false)
    } catch {
      setFailed(true)
    }
  }

  // Reorder, then show the new order at once rather than at the next poll.
  async function move(fn, to) {
    if (moving) return
    setMoving(true)
    try { await fn(to); await load() } catch (e) { alertOnce(e.message) }
    finally { setMoving(false) }
  }

  const moveButtons = (fn, { first, last, bottom = false }) => (
    <span style={{ display: 'inline-flex', gap: 4 }}>
      {[['top', 'Top', first], ['up', 'Up', first], ['down', 'Down', last],
        ...(bottom ? [['bottom', 'Bottom', last]] : [])].map(([to, label, off]) => (
        <button key={to} onClick={() => move(fn, to)} disabled={moving || off}
                style={{ fontSize: 12, padding: '2px 7px' }}
                data-track={`Queue: move ${to}`}>
          {label}
        </button>
      ))}
    </span>
  )

  useEffect(() => {
    let alive = true
    let timer
    const tick = async () => {
      try {
        const q = await fetchQueue()
        if (!alive) return
        setQueue(q)
        setFailed(false)
      } catch {
        if (alive) setFailed(true)
      }
      if (alive) timer = setTimeout(tick, 3000)
    }
    tick()
    return () => { alive = false; clearTimeout(timer) }
  }, [])

  async function stopJob(job) {
    if (!window.confirm(`Stop "${job.label}"? Work already done is kept.`)) return
    try { await cancelJob(job.id) } catch (e) { alertOnce(e.message) }
  }

  async function stopLots() {
    if (!window.confirm(`Take all ${queue.lots.total} waiting items out of the queue? `
                        + 'The one in progress will finish.')) return
    try {
      const r = await cancelEnrichment()
      alert(`Removed ${r.cancelled} items from the queue.`)
    } catch (e) { alertOnce(e.message) }
  }

  if (!queue) {
    return failed
      ? <p style={{ color: 'var(--muted)' }}>Couldn't load the queue — retrying…</p>
      : <p style={{ color: 'var(--muted)' }}><span className="spinner" /> Loading the queue…</p>
  }

  const { jobs, lots } = queue
  const waitingJobs = jobs.filter((j) => j.state === 'pending' && !j.cancelled)
  const waitingLots = lots.items.filter((l) => !l.stage)
  // Only the first few hundred are listed; the last one shown is not the
  // last in line when more are waiting beyond it.
  const lastShownIsLast = lots.total <= lots.items.length
  if (!jobs.length && !lots.total) {
    return (
      <div className="empty-state">
        <div><strong>Nothing left in the queue.</strong></div>
        <div style={{ marginTop: 4 }}>Everything you started has finished.</div>
      </div>
    )
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      {jobs.length > 0 && (
        <section>
          <h3 style={{ margin: '0 0 8px', fontSize: 15 }}>Jobs ({jobs.length})</h3>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {jobs.map((job) => {
              const pct = job.total > 0
                ? Math.min(100, Math.round(((job.current || 0) / job.total) * 100)) : null
              return (
                <div key={job.id} className="card" style={{ padding: '10px 12px' }}>
                  <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, flexWrap: 'wrap' }}>
                    <strong style={{ fontSize: 14 }}>{job.label}</strong>
                    <span style={{ color: 'var(--muted)', fontSize: 13 }}>
                      {job.cancelled ? 'stopping…' : jobProgress(job)}
                    </span>
                    {job.state === 'pending' && !job.cancelled && waitingJobs.length > 1 && (
                      <span style={{ marginLeft: 'auto' }}>
                        {moveButtons((to) => moveQueuedJob(job.id, to), {
                          first: waitingJobs[0].id === job.id,
                          last: waitingJobs[waitingJobs.length - 1].id === job.id,
                        })}
                      </span>
                    )}
                    {!job.cancelled && (
                      <button onClick={() => stopJob(job)}
                              style={{ marginLeft: job.state === 'pending' && waitingJobs.length > 1 ? 0 : 'auto',
                                       fontSize: 12, padding: '2px 8px' }}>
                        Cancel
                      </button>
                    )}
                  </div>
                  {pct !== null && (
                    <div style={{ height: 6, borderRadius: 3, background: 'var(--badge-bg)',
                                  overflow: 'hidden', margin: '8px 0 4px' }}>
                      <div style={{ height: '100%', width: `${pct}%`, background: 'var(--link)' }} />
                    </div>
                  )}
                  {job.detail && (
                    <div style={{ fontSize: 12, color: 'var(--muted)' }}>Now: {job.detail}</div>
                  )}
                  {job.next_up.length > 0 && (
                    <details style={{ fontSize: 13, marginTop: 4 }}>
                      <summary style={{ cursor: 'pointer', color: 'var(--muted)' }}>
                        Next up ({(job.remaining ?? job.next_up.length).toLocaleString()} left)
                      </summary>
                      <ol style={{ margin: '6px 0 0', paddingLeft: 22 }}>
                        {job.next_up.map((it, i) => (
                          <li key={it.id ?? i} style={{ marginBottom: 3 }}>
                            <span style={{ marginRight: 6 }}>{it.name}</span>
                            {/* This is the list the user is looking at when
                                they want something priced sooner, so the
                                controls belong here rather than only on the
                                rare pending job. */}
                            {job.next_up_movable && job.next_up.length > 1 && (
                              moveButtons((to) => moveJobItem(job.id, it.id, to), {
                                first: i === 0,
                                last: i === job.next_up.length - 1 && !job.next_up_more,
                                bottom: false,
                              })
                            )}
                          </li>
                        ))}
                      </ol>
                      {job.next_up_more > 0 && (
                        <div style={{ color: 'var(--muted)', marginTop: 4 }}>
                          …and {job.next_up_more.toLocaleString()} more
                        </div>
                      )}
                    </details>
                  )}
                </div>
              )
            })}
          </div>
        </section>
      )}

      {lots.total > 0 && (
        <section>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, marginBottom: 8 }}>
            <h3 style={{ margin: 0, fontSize: 15 }}>
              Items waiting to be priced ({lots.total.toLocaleString()})
            </h3>
            <button onClick={stopLots} style={{ marginLeft: 'auto', fontSize: 12, padding: '2px 8px' }}>
              Cancel all
            </button>
          </div>
          {isMobile ? (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              {lots.items.map((l, i) => (
                <div key={l.lot_db_id} className="card" style={{ padding: '8px 10px', fontSize: 13 }}>
                  <div>
                    <span style={{ color: 'var(--muted)', marginRight: 6 }}>{i + 1}.</span>
                    {l.lot_link
                      ? <a href={l.lot_link} target="_blank" rel="noreferrer">{l.title}</a>
                      : l.title}
                  </div>
                  <div style={{ color: 'var(--muted)', fontSize: 12, marginTop: 2 }}>
                    {l.auction_name || '—'} · {l.stage
                      ? <strong style={{ color: 'var(--text)' }}>working now: {l.stage}</strong>
                      : `waiting to ${taskLabel(l.task)}`}
                  </div>
                  {!l.stage && waitingLots.length > 1 && (
                    <div style={{ marginTop: 6 }}>
                      {moveButtons((to) => moveQueuedLot(l.lot_db_id, to), {
                        first: waitingLots[0].lot_db_id === l.lot_db_id,
                        last: lastShownIsLast && waitingLots[waitingLots.length - 1].lot_db_id === l.lot_db_id,
                        bottom: true,
                      })}
                    </div>
                  )}
                </div>
              ))}
            </div>
          ) : (
            <table className="data-table">
              <thead>
                <tr><th className="num">#</th><th>Item</th><th>Auction</th><th>What's left</th><th>Order</th></tr>
              </thead>
              <tbody>
                {lots.items.map((l, i) => (
                  <tr key={l.lot_db_id}>
                    <td className="num" style={{ color: 'var(--muted)' }}>{i + 1}</td>
                    <td>
                      {l.lot_link
                        ? <a href={l.lot_link} target="_blank" rel="noreferrer">{l.title}</a>
                        : l.title}
                    </td>
                    <td style={{ fontSize: 12 }}>{l.auction_name || '—'}</td>
                    <td style={{ fontSize: 12 }}>
                      {l.stage
                        ? <strong>working now: {l.stage}</strong>
                        : <span style={{ color: 'var(--muted)' }}>waiting to {taskLabel(l.task)}</span>}
                    </td>
                    <td style={{ whiteSpace: 'nowrap' }}>
                      {!l.stage && waitingLots.length > 1 && moveButtons(
                        (to) => moveQueuedLot(l.lot_db_id, to), {
                          first: waitingLots[0].lot_db_id === l.lot_db_id,
                          last: lastShownIsLast && waitingLots[waitingLots.length - 1].lot_db_id === l.lot_db_id,
                          bottom: true,
                        })}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {lots.total > lots.items.length && (
            <div style={{ color: 'var(--muted)', fontSize: 13, marginTop: 6 }}>
              Showing the next {lots.items.length.toLocaleString()} of {lots.total.toLocaleString()}.
            </div>
          )}
        </section>
      )}
    </div>
  )
}
