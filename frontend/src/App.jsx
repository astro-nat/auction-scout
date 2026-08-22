import { useCallback, useEffect, useState } from 'react'
import { fetchLots, fetchAuctions, fetchCategories, scanAuctions, importLots, enrichAll } from './api'
import LotTable from './components/LotTable'
import StatusBar from './components/StatusBar'
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
  // Scan filters — mirrors hibid.com's own search options
  const [categories, setCategories] = useState([])
  // Names of every auction we've seen this session. The visible `auctions`
  // list gets replaced by scan results, but lots can belong to any auction —
  // without this they'd render as "—" (looking unattached).
  const [auctionIndex, setAuctionIndex] = useState({})
  const [auctionLimit, setAuctionLimit] = useState(50)
  const [scan, setScan] = useState({
    search_text: '', category_id: -1, auction_type: 'ALL',
    status: 'OPEN', zip: '', radius_miles: 25,
  })

  const loadLots = useCallback(() => {
    fetchLots({
      auctionId: selectedAuction,
      boloOnly: filters.boloOnly,
      roiStatus: filters.roiStatus || undefined,
    }).then(setLots).catch(console.error)
  }, [selectedAuction, filters])

  const rememberAuctions = useCallback((list) => {
    setAuctionIndex((prev) => {
      const next = { ...prev }
      for (const a of list) next[a.id] = a.name
      return next
    })
    return list
  }, [])

  useEffect(() => {
    fetchAuctions().then(rememberAuctions).then(setAuctions).catch(console.error)
  }, [rememberAuctions])
  useEffect(() => { fetchCategories().then(setCategories).catch(console.error) }, [])
  useEffect(() => { loadLots() }, [loadLots])

  async function handleScan() {
    setBusy('Scanning HiBid…')
    try {
      const found = await scanAuctions({
        ...scan,
        zip: scan.zip || undefined,
        category_id: Number(scan.category_id),
        radius_miles: Number(scan.radius_miles),
      })
      rememberAuctions(found)
      setAuctions(found)
      setAuctionLimit(50)
    } catch (e) { alert(e.message) } finally { setBusy('') }
  }

  // Called when the status bar sees the server go idle — pull fresh data so
  // finished imports/enrichments appear without a manual refresh.
  const refreshAll = useCallback(() => {
    // Only refresh the name index here — replacing the visible list would
    // wipe the user's scan results out from under them.
    fetchAuctions().then(rememberAuctions).catch(console.error)
    loadLots()
  }, [loadLots, rememberAuctions])

  function setScanField(field, value) {
    setScan((prev) => ({ ...prev, [field]: value }))
  }

  // When the scan had a category filter, Import pulls only matching lots.
  const scanCategoryId = Number(scan.category_id)
  const scanCategoryName = categories.find((c) => c.id === scanCategoryId)?.name

  // The stored count is only meaningful for the category it was counted for.
  const hasCategoryCount = (a) =>
    scanCategoryId !== -1 &&
    a.category_lot_count != null &&
    a.category_count_for === scanCategoryId

  function importLabel(a) {
    if (hasCategoryCount(a)) {
      // Keep it short on phones — the full category name blew the button
      // out of the card and pushed it off screen.
      return isMobile
        ? `Import ${a.category_lot_count}`
        : `Import ${a.category_lot_count} ${scanCategoryName ?? 'matching'}`
    }
    return 'Import'
  }

  async function handleImport(auctionId) {
    setBusy('Importing lots…')
    try {
      const r = await importLots(auctionId, scanCategoryId)
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

  // An auction is "hot" when its gold-mine lots add up to real money.
  const isHotAuction = (a) => Number(a.gold_profit ?? 0) >= 100
  const goldBadge = (a) =>
    a.gold_count > 0
      ? `🟢 ${a.gold_count} gold · ~$${Number(a.gold_profit).toFixed(0)} potential profit`
      : null

  // Stamp each lot with its auction's name so the table can show/filter it.
  const auctionNames = { ...auctionIndex, ...Object.fromEntries(auctions.map((a) => [a.id, a.name])) }
  const visibleLots = lots
    .filter((l) => {
      if (hideLowValue && isConfirmedLowValue(l)) return false
      if (hideHardShip && l.logistics_ease === 'HARD') return false
      return true
    })
    .map((l) => ({ ...l, auction_name: auctionNames[l.auction_id] ?? '—' }))
  const hiddenCount = lots.length - visibleLots.length

  return (
    <div style={{ fontFamily: 'system-ui' }}>
      <StatusBar onQuiet={refreshAll} />
      <div style={{ padding: isMobile ? '0.75rem' : '2rem' }}>
      <h1 style={{ fontSize: isMobile ? 24 : undefined }}>AuctionScout</h1>

      <section style={{ marginBottom: '1.5rem' }}>
        {/* Form wrapper: pressing Enter in any filter field runs the scan */}
        <form onSubmit={(ev) => { ev.preventDefault(); handleScan() }}>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginBottom: 8 }}>
          <input
            value={scan.search_text}
            onChange={(ev) => setScanField('search_text', ev.target.value)}
            placeholder="Keyword (auction name/content)…"
            style={{ flex: isMobile ? '1 1 100%' : '1 1 240px', padding: 6, fontSize: 14 }}
          />
          <select value={scan.category_id} onChange={(ev) => setScanField('category_id', ev.target.value)}
                  style={{ flex: 1, padding: 6, fontSize: 14, minWidth: 140 }}>
            <option value={-1}>All categories</option>
            {categories.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
          <select value={scan.auction_type} onChange={(ev) => setScanField('auction_type', ev.target.value)}
                  style={{ flex: 1, padding: 6, fontSize: 14, minWidth: 120 }}>
            <option value="ALL">All auction types</option>
            <option value="ONLINE">Online Only</option>
            <option value="WEBCAST">Live Webcast</option>
            <option value="ABSENTEE">Absentee</option>
            <option value="LISTING">Listing Only</option>
          </select>
          <select value={scan.status} onChange={(ev) => setScanField('status', ev.target.value)}
                  style={{ flex: 1, padding: 6, fontSize: 14, minWidth: 110 }}>
            <option value="OPEN">Open</option>
            <option value="CLOSING">Closing soon</option>
            <option value="HOT">Hot</option>
            <option value="ALL">Any status</option>
          </select>
          <input
            value={scan.zip}
            onChange={(ev) => setScanField('zip', ev.target.value)}
            placeholder="Zip (default 77058)"
            style={{ flex: 1, padding: 6, fontSize: 14, minWidth: 100, maxWidth: 150 }}
          />
          <select value={scan.radius_miles} onChange={(ev) => setScanField('radius_miles', ev.target.value)}
                  style={{ flex: 1, padding: 6, fontSize: 14, minWidth: 100, maxWidth: 130 }}>
            <option value={25}>25 miles</option>
            <option value={50}>50 miles</option>
            <option value={100}>100 miles</option>
            <option value={250}>250 miles</option>
            <option value={500}>500 miles</option>
            <option value={-1}>Anywhere</option>
          </select>
        </div>
        <button type="submit" disabled={!!busy}
                style={isMobile ? { width: '100%', padding: 10, fontSize: 15 } : undefined}>
          Scan auctions
        </button>
        </form>
        {busy && <span style={{ marginLeft: '1rem' }}>{busy}</span>}
        {auctions.length > 0 && (isMobile ? (
          <details style={{ marginTop: '0.75rem' }} open={!selectedAuction}>
            <summary style={{ fontWeight: 600, padding: '4px 0' }}>
              Auctions ({auctions.length})
            </summary>
            {auctions.slice(0, auctionLimit).map((a) => (
              <div key={a.id} style={{
                border: isHotAuction(a) ? '2px solid #2e9e4f' : '1px solid var(--border)',
                borderRadius: 8, padding: 10, marginTop: 8,
                background: isHotAuction(a) ? 'var(--gold-bg)'
                  : selectedAuction === a.id ? 'var(--highlight)' : 'var(--card-bg)',
              }}>
                <div style={{ fontWeight: 600 }}>
                  <a href={a.source_url} target="_blank" rel="noreferrer">{a.name}</a>
                </div>
                <div style={{ fontSize: 13, color: 'var(--muted)', margin: '4px 0' }}>
                  {a.city}, {a.state} · {a.lot_count ?? '—'} lots
                  · closes {a.closing_date ? new Date(a.closing_date).toLocaleDateString() : '—'}
                  {a.buyer_premium_mult ? ` · ${Math.round((a.buyer_premium_mult - 1) * 100)}% premium` : ''}
                  {hasCategoryCount(a)
                    ? ` · ${a.category_lot_count} in ${scanCategoryName ?? 'category'}` : ''}
                </div>
                {goldBadge(a) && (
                  <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 4 }}>{goldBadge(a)}</div>
                )}
                <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                  <button style={{ flex: '1 1 90px', minWidth: 90, padding: 8 }}
                          onClick={() => handleImport(a.id)} disabled={!!busy}>
                    {importLabel(a)}
                  </button>
                  {a.imported_at && (
                    <>
                      <button style={{ flex: '1 1 70px', minWidth: 70, padding: 8 }}
                              onClick={() => setSelectedAuction(a.id)}>View</button>
                      <button style={{ flex: '1 1 90px', minWidth: 90, padding: 8 }}
                              onClick={() => handleEnrichAll(a.id)}>Enrich all</button>
                    </>
                  )}
                </div>
              </div>
            ))}
            {auctions.length > auctionLimit && (
              <button style={{ width: '100%', padding: 8, marginTop: 8 }}
                      onClick={() => setAuctionLimit((n) => n + 50)}>
                Show more auctions ({auctions.length - auctionLimit} more)
              </button>
            )}
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
              {auctions.slice(0, auctionLimit).map((a) => (
                <tr key={a.id} style={{
                  background: isHotAuction(a) ? 'var(--gold-bg)'
                    : selectedAuction === a.id ? 'var(--highlight)' : undefined,
                }}>
                  <td style={{ paddingRight: 12 }}>
                    <a href={a.source_url} target="_blank" rel="noreferrer">{a.name}</a>
                    {goldBadge(a) && (
                      <div style={{ fontSize: 12, fontWeight: 600 }}>{goldBadge(a)}</div>
                    )}
                  </td>
                  <td style={{ paddingRight: 12 }}>{a.city}, {a.state} ({a.source})</td>
                  <td style={{ textAlign: 'center' }}>
                    {a.lot_count ?? '—'}
                    {hasCategoryCount(a) && (
                      <div style={{ fontSize: 11, color: 'var(--muted)' }}>{a.category_lot_count} match</div>
                    )}
                  </td>
                  <td>{a.closing_date ? new Date(a.closing_date).toLocaleDateString() : '—'}</td>
                  <td style={{ textAlign: 'center' }}>
                    {a.buyer_premium_mult ? `${Math.round((a.buyer_premium_mult - 1) * 100)}%` : '—'}
                  </td>
                  <td><button onClick={() => handleImport(a.id)} disabled={!!busy}>{importLabel(a)}</button></td>
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
        {!isMobile && auctions.length > auctionLimit && (
          <button style={{ marginTop: 8 }} onClick={() => setAuctionLimit((n) => n + 50)}>
            Show more auctions ({auctions.length - auctionLimit} more)
          </button>
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

        <button style={{ marginLeft: '1rem' }} onClick={loadLots}>Refresh</button>
      </section>

      {selectedAuction && (
        <div style={{
          display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap',
          background: 'var(--highlight)', border: '1px solid var(--border)',
          borderRadius: 6, padding: '8px 10px', marginBottom: 10,
        }}>
          <span>
            Viewing <strong>{auctionNames[selectedAuction] ?? 'this auction'}</strong>
            {' '}— {lots.length} lot{lots.length === 1 ? '' : 's'}
          </span>
          <button onClick={() => setSelectedAuction(null)}>Show all auctions</button>
        </div>
      )}
      <LotTable lots={visibleLots} onLotUpdated={handleLotUpdated} onRefresh={loadLots} />
      </div>
    </div>
  )
}
