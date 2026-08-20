import { useMemo, useState } from 'react'
import { enrichLot, inspectLot, fetchLot } from '../api'

const cell = { padding: '4px 10px', borderBottom: '1px solid #ddd' }

function money(v) {
  if (v === null || v === undefined) return '—'
  return `$${Number(v).toFixed(2)}`
}

const num = (v) => (v === null || v === undefined ? null : Number(v))

// Column definitions: label + accessor used for sorting. Numeric accessors
// return numbers (or null); everything else sorts as lowercase strings.
const COLUMNS = [
  { key: 'title', label: 'Title', get: (l) => l.title?.toLowerCase() },
  { key: 'category', label: 'Category', get: (l) => l.category?.toLowerCase() },
  { key: 'bid', label: 'Bid', get: (l) => num(l.current_bid) },
  { key: 'est_cost', label: 'Est Cost', get: (l) => num(l.est_cost) },
  { key: 'ship', label: 'Ship', get: (l) => l.logistics_ease },
  { key: 'bolo', label: 'BOLO', get: (l) => l.enrichment?.bolo_brand?.toLowerCase() },
  { key: 'est_resale', label: 'Est Resale', get: (l) => num(l.enrichment?.est_resale) },
  { key: 'max_bid', label: 'Max Bid', get: (l) => num(l.enrichment?.max_bid) },
  { key: 'verdict', label: 'Verdict', get: (l) => l.enrichment?.verdict },
  { key: 'status', label: 'Status', get: (l) => l.enrichment?.status },
]

export default function LotTable({ lots, onLotUpdated }) {
  const [pollingIds, setPollingIds] = useState(new Set())
  const [sort, setSort] = useState({ key: null, dir: 1 })

  const sorted = useMemo(() => {
    if (!sort.key) return lots
    const col = COLUMNS.find((c) => c.key === sort.key)
    return [...lots].sort((a, b) => {
      const av = col.get(a)
      const bv = col.get(b)
      // null/undefined always sorts last regardless of direction
      if (av == null && bv == null) return 0
      if (av == null) return 1
      if (bv == null) return -1
      if (av < bv) return -sort.dir
      if (av > bv) return sort.dir
      return 0
    })
  }, [lots, sort])

  function handleSort(key) {
    setSort((prev) =>
      prev.key === key ? { key, dir: -prev.dir } : { key, dir: 1 }
    )
  }

  async function handleEnrich(lotId) {
    await enrichLot(lotId)
    setPollingIds((prev) => new Set(prev).add(lotId))
    poll(lotId)
  }

  async function handleInspect(lotId) {
    await inspectLot(lotId)
    setPollingIds((prev) => new Set(prev).add(lotId))
    poll(lotId)
  }

  function poll(lotId) {
    const interval = setInterval(async () => {
      const updated = await fetchLot(lotId)
      if (updated.enrichment?.status === 'success' || updated.enrichment?.status === 'failed') {
        clearInterval(interval)
        setPollingIds((prev) => {
          const next = new Set(prev)
          next.delete(lotId)
          return next
        })
      }
      onLotUpdated(updated)
    }, 3000)
  }

  if (!lots.length) return <p>No lots yet — scan auctions and import one above.</p>

  return (
    <table style={{ borderCollapse: 'collapse', fontSize: 14 }}>
      <thead>
        <tr>
          {COLUMNS.map((c) => (
            <th
              key={c.key}
              style={{ ...cell, cursor: 'pointer', userSelect: 'none', whiteSpace: 'nowrap' }}
              onClick={() => handleSort(c.key)}
              title="Click to sort"
            >
              {c.label}
              {sort.key === c.key ? (sort.dir === 1 ? ' ▲' : ' ▼') : ''}
            </th>
          ))}
          <th style={cell}></th>
        </tr>
      </thead>
      <tbody>
        {sorted.map((lot) => {
          const e = lot.enrichment || {}
          const gold = e.roi_status === 'GOLD MINE'
          return (
            <tr key={lot.lot_id} style={gold ? { background: '#e6ffe6' } : undefined}>
              <td style={cell}>
                <a href={lot.lot_link} target="_blank" rel="noreferrer">{lot.title}</a>
                {e.enriched_title && e.enriched_title !== lot.title && (
                  <div style={{ color: '#666', fontSize: 12 }}>→ {e.enriched_title}</div>
                )}
                {e.notes && e.ai_source === 'vision-itemized' && (
                  <details style={{ fontSize: 12, color: '#444' }}>
                    <summary>itemized breakdown</summary>
                    <pre style={{ whiteSpace: 'pre-wrap', margin: 0 }}>{e.notes}</pre>
                  </details>
                )}
              </td>
              <td style={cell}>{lot.category}</td>
              <td style={cell}>{money(lot.current_bid)} / {money(lot.next_bid)}</td>
              <td style={cell}>{money(lot.est_cost)}</td>
              <td style={cell}>{lot.logistics_ease}</td>
              <td style={cell}>
                {e.bolo_brand
                  ? `${e.bolo_brand} (T${e.bolo_tier ?? '?'} ${e.bolo_confidence ?? ''})`
                  : '—'}
              </td>
              <td style={cell}>
                {money(e.est_resale)}
                {e.comp_count > 0 && (
                  <span style={{ color: '#666', fontSize: 12 }}> ({e.comp_count})</span>
                )}
              </td>
              <td style={cell}>{money(e.max_bid)}</td>
              <td style={cell}>{gold ? '🟢' : e.roi_status === 'PASS' ? '🔴' : ''} {e.verdict ?? ''}</td>
              <td style={cell}>
                {pollingIds.has(lot.lot_id) ? 'enriching…' : e.status}
                {e.status === 'failed' && e.error_message && (
                  <div style={{ color: '#a00', fontSize: 12 }}>{e.error_message.slice(0, 80)}</div>
                )}
              </td>
              <td style={{ ...cell, whiteSpace: 'nowrap' }}>
                <button onClick={() => handleEnrich(lot.lot_id)}>Enrich</button>{' '}
                <button onClick={() => handleInspect(lot.lot_id)} title="Identify and price each item in the photo individually">
                  Inspect
                </button>
              </td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}
