import { useCallback, useEffect, useState } from 'react'
import { fetchLots, fetchAuctions, scanAuctions, importLots, enrichAll } from './api'
import LotTable from './components/LotTable'
import useMediaQuery from './useMediaQuery'

export default function App() {
  const isMobile = useMediaQuery('(max-width: 768px)')
  const [auctions, setAuctions] = useState([])
  const [selectedAuction, setSelectedAuction] = useState(null)
  const [lots, setLots] = useState([])
  const [filters, setFilters] = useState({ boloOnly: false, roiStatus: '' })
  const [hideLowValue, setHideLowValue] = useState(true)
  const [lowValueCutoff, setLowValueCutoff] = useState(25)
  const [hideHardShip, setHideHardShip] = useState(false)
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

  const visibleLots = lots.filter((l) => {
    if (hideLowValue && isConfirmedLowValue(l)) return false
    if (hideHardShip && l.logistics_ease === 'HARD') return false
    return true
  })
  const hiddenCount = lots.length - visibleLots.length

  return (
    <div style={{ padding: isMobile ? '0.75rem' : '2rem', fontFamily: 'system-ui' }}>
      <h1 style={{ fontSize: isMobile ? 24 : undefined }}>AuctionScout</h1>

      <section style={{ marginBottom: '1.5rem' }}>
        <button onClick={handleScan} disabled={!!busy}
                style={isMobile ? { width: '100%', padding: 10, fontSize: 15 } : undefined}>
          Scan nearby auctions
        </button>
        {busy && <span style={{ marginLeft: '1rem' }}>{busy}</span>}
        {auctions.length > 0 && (isMobile ? (
          <details style={{ marginTop: '0.75rem' }} open={!selectedAuction}>
            <summary style={{ fontWeight: 600, padding: '4px 0' }}>
              Auctions ({auctions.length})
            </summary>
            {auctions.map((a) => (
              <div key={a.id} style={{
                border: '1px solid var(--border)', borderRadius: 8, padding: 10, marginTop: 8,
                background: selectedAuction === a.id ? 'var(--highlight)' : 'var(--card-bg)',
              }}>
                <div style={{ fontWeight: 600 }}>
                  <a href={a.source_url} target="_blank" rel="noreferrer">{a.name}</a>
                </div>
                <div style={{ fontSize: 13, color: 'var(--muted)', margin: '4px 0' }}>
                  {a.city}, {a.state} · {a.lot_count ?? '—'} lots
                  · closes {a.closing_date ? new Date(a.closing_date).toLocaleDateString() : '—'}
                  {a.buyer_premium_mult ? ` · ${Math.round((a.buyer_premium_mult - 1) * 100)}% premium` : ''}
                </div>
                <div style={{ display: 'flex', gap: 8 }}>
                  <button style={{ flex: 1, padding: 8 }} onClick={() => handleImport(a.id)} disabled={!!busy}>
                    Import
                  </button>
                  {a.imported_at && (
                    <>
                      <button style={{ flex: 1, padding: 8 }} onClick={() => setSelectedAuction(a.id)}>View</button>
                      <button style={{ flex: 1, padding: 8 }} onClick={() => handleEnrichAll(a.id)}>Enrich all</button>
                    </>
                  )}
                </div>
              </div>
            ))}
          </details>
        ) : (
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
                <tr key={a.id} style={{ background: selectedAuction === a.id ? 'var(--highlight)' : undefined }}>
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
        ))}
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
        <label style={{ marginLeft: '1rem' }}>
          <input
            type="checkbox"
            checked={hideHardShip}
            onChange={(ev) => setHideHardShip(ev.target.checked)}
          /> Hide HARD ship
        </label>
        {(hideLowValue || hideHardShip) && hiddenCount > 0 && (
          <span style={{ marginLeft: '0.5rem', color: 'var(--muted)' }}>
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

      <LotTable lots={visibleLots} onLotUpdated={handleLotUpdated} onRefresh={loadLots} />
    </div>
  )
}
