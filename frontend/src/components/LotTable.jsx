import { useState } from 'react'
import { enrichLot, fetchLot } from '../api'

const cell = { padding: '4px 10px', borderBottom: '1px solid #ddd' }

function money(v) {
  if (v === null || v === undefined) return '—'
  return `$${Number(v).toFixed(2)}`
}

export default function LotTable({ lots, onLotUpdated }) {
  const [pollingIds, setPollingIds] = useState(new Set())

  async function handleEnrich(lotId) {
    await enrichLot(lotId)
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
          <th style={cell}>Title</th>
          <th style={cell}>Category</th>
          <th style={cell}>Bid</th>
          <th style={cell}>Est Cost</th>
          <th style={cell}>Ship</th>
          <th style={cell}>BOLO</th>
          <th style={cell}>Est Resale</th>
          <th style={cell}>Max Bid</th>
          <th style={cell}>Verdict</th>
          <th style={cell}>Status</th>
          <th style={cell}></th>
        </tr>
      </thead>
      <tbody>
        {lots.map((lot) => {
          const e = lot.enrichment || {}
          const gold = e.roi_status === 'GOLD MINE'
          return (
            <tr key={lot.lot_id} style={gold ? { background: '#e6ffe6' } : undefined}>
              <td style={cell}>
                <a href={lot.lot_link} target="_blank" rel="noreferrer">{lot.title}</a>
                {e.enriched_title && e.enriched_title !== lot.title && (
                  <div style={{ color: '#666', fontSize: 12 }}>→ {e.enriched_title}</div>
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
              <td style={cell}>
                <button onClick={() => handleEnrich(lot.lot_id)}>Enrich</button>
              </td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}
