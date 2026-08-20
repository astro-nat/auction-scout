import { useState } from 'react'
import { enrichLot, fetchLot } from '../api'

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

  return (
    <table>
      <thead>
        <tr>
          <th>Title</th>
          <th>Category</th>
          <th>Next Bid</th>
          <th>Status</th>
          <th>Brand / Tier</th>
          <th>Target Buy</th>
          <th></th>
        </tr>
      </thead>
      <tbody>
        {lots.map((lot) => (
          <tr key={lot.lot_id}>
            <td>{lot.title}</td>
            <td>{lot.category}</td>
            <td>{lot.next_bid}</td>
            <td>{pollingIds.has(lot.lot_id) ? 'enriching…' : lot.enrichment?.status}</td>
            <td>{lot.enrichment?.bolo_brand} {lot.enrichment?.bolo_tier}</td>
            <td>{lot.enrichment?.target_buy_price ?? '—'}</td>
            <td>
              <button onClick={() => handleEnrich(lot.lot_id)}>Enrich</button>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}
