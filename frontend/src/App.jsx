import { useCallback, useEffect, useState } from 'react'
import { fetchLots, fetchAuctions, scanAuctions, importLots, enrichAll } from './api'
import LotTable from './components/LotTable'

export default function App() {
  const [auctions, setAuctions] = useState([])
  const [selectedAuction, setSelectedAuction] = useState(null)
  const [lots, setLots] = useState([])
  const [filters, setFilters] = useState({ boloOnly: false, roiStatus: '' })
  const [hideLowValue, setHideLowValue] = useState(true)
  const [lowValueCutoff, setLowValueCutoff] = useState(25)
  const [busy, setBusy] = useState('')

  const loadLots = useCallback(() => {
    fetchLots({
      auctionId: selectedAuction,
      boloOnly: filters.boloOnly,
      roiStatus: filters.roiStatus || undefined,
    }).then(setLots).catch(console.error)
  }, [selectedAuction, filters])

  useEffect(() => { fetchAuctions().then(setAuctions).catch(console.error) }, [])
  useEffect(() => { loadLots() }, [loadLots])

  async function handleScan() {
    setBusy('Scanning HiBid for nearby auctions…')
    try {
      const found = await scanAuctions()
      setAuctions(found)
    } catch (e) { alert(e.message) } finally { setBusy('') }
  }

  async function handleImport(auctionId) {
    setBusy('Importing lots…')
    try {
      const r = await importLots(auctionId)
      setBusy('')
      setSelectedAuction(auctionId)
      alert(`Imported ${r.created} new lots (${r.updated} updated) of ${r.fetched} fetched`)
    } catch (e) { alert(e.message); setBusy('') }
  }

  async function handleEnrichAll(auctionId) {
    try {
      const r = await enrichAll(auctionId)
      alert(`Queued ${r.queued} lots for enrichment — statuses will update as they finish`)
      loadLots()
    } catch (e) { alert(e.message) }
  }

  function handleLotUpdated(updated) {
    setLots((prev) => prev.map((l) => (l.lot_id === updated.lot_id ? updated : l)))
  }

  // "Confirmed low-value" = 3+ comps agree the resale is under the cutoff.
  // Unenriched lots stay visible — unknown is not the same as confirmed cheap.
  function isConfirmedLowValue(lot) {
    const e = lot.enrichment
    return (
      e?.est_resale != null &&
      e.comp_count >= 3 &&
      Number(e.est_resale) < lowValueCutoff
    )
  }

  const visibleLots = hideLowValue ? lots.filter((l) => !isConfirmedLowValue(l)) : lots
  const hiddenCount = lots.length - visibleLots.length

  return (
    <div style={{ padding: '2rem', fontFamily: 'system-ui' }}>
      <h1>AuctionScout</h1>

      <section style={{ marginBottom: '1.5rem' }}>
        <button onClick={handleScan} disabled={!!busy}>Scan nearby auctions</button>
        {busy && <span style={{ marginLeft: '1rem' }}>{busy}</span>}
        {auctions.length > 0 && (
          <table style={{ marginTop: '0.75rem', borderCollapse: 'collapse' }}>
            <thead>
              <tr>
                <th style={{ textAlign: 'left', paddingRight: 12 }}>Auction</th>
                <th style={{ textAlign: 'left', paddingRight: 12 }}>Where</th>
                <th>Lots</th><th>Closes</th><th>Premium</th><th></th><th></th>
              </tr>
            </thead>
            <tbody>
              {auctions.map((a) => (
                <tr key={a.id} style={{ background: selectedAuction === a.id ? '#eef' : undefined }}>
                  <td style={{ paddingRight: 12 }}>
                    <a href={a.source_url} target="_blank" rel="noreferrer">{a.name}</a>
                  </td>
                  <td style={{ paddingRight: 12 }}>{a.city}, {a.state} ({a.source})</td>
                  <td style={{ textAlign: 'center' }}>{a.lot_count ?? '—'}</td>
                  <td>{a.closing_date ? new Date(a.closing_date).toLocaleDateString() : '—'}</td>
                  <td style={{ textAlign: 'center' }}>
                    {a.buyer_premium_mult ? `${Math.round((a.buyer_premium_mult - 1) * 100)}%` : '—'}
                  </td>
                  <td><button onClick={() => handleImport(a.id)} disabled={!!busy}>Import lots</button></td>
                  <td>
                    {a.imported_at && (
                      <>
                        <button onClick={() => setSelectedAuction(a.id)}>View</button>{' '}
                        <button onClick={() => handleEnrichAll(a.id)}>Enrich all</button>
                      </>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section style={{ marginBottom: '0.75rem' }}>
        <label>
          <input
            type="checkbox"
            checked={filters.boloOnly}
            onChange={(ev) => setFilters((f) => ({ ...f, boloOnly: ev.target.checked }))}
          /> BOLO matches only
        </label>
        {' '}
        <label style={{ marginLeft: '1rem' }}>
          <input
            type="checkbox"
            checked={filters.roiStatus === 'GOLD MINE'}
            onChange={(ev) => setFilters((f) => ({ ...f, roiStatus: ev.target.checked ? 'GOLD MINE' : '' }))}
          /> Gold mines only
        </label>
        <label style={{ marginLeft: '1rem' }}>
          <input
            type="checkbox"
            checked={hideLowValue}
            onChange={(ev) => setHideLowValue(ev.target.checked)}
          /> Hide confirmed low-value (resale under $
          <input
            type="number"
            value={lowValueCutoff}
            onChange={(ev) => setLowValueCutoff(Number(ev.target.value) || 0)}
            style={{ width: 50 }}
          /> with 3+ comps)
        </label>
        {hideLowValue && hiddenCount > 0 && (
          <span style={{ marginLeft: '0.5rem', color: '#666' }}>
            {hiddenCount} hidden
          </span>
        )}
        {selectedAuction && (
          <button style={{ marginLeft: '1rem' }} onClick={() => setSelectedAuction(null)}>
            Show all auctions' lots
          </button>
        )}
        <button style={{ marginLeft: '1rem' }} onClick={loadLots}>Refresh</button>
      </section>

      <LotTable lots={visibleLots} onLotUpdated={handleLotUpdated} />
    </div>
  )
}
