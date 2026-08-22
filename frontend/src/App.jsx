import { useCallback, useEffect, useState } from 'react'
import { fetchLots, fetchLotCount, fetchAuctions, fetchCategories, scanAuctions, importLots, enrichAll } from './api'
import LotTable from './components/LotTable'
import StatusBar from './components/StatusBar'
import useMediaQuery from './useMediaQuery'

export default function App() {
  const isMobile = useMediaQuery('(max-width: 768px)')
  // Two jobs, two screens: finding auctions vs working through what you've
  // imported. Mixing them on one page made both harder to read.
  const [view, setView] = useState('auctions')
  const [auctions, setAuctions] = useState([])
  const [selectedAuction, setSelectedAuction] = useState(null)
  const [lots, setLots] = useState([])
  const [filters, setFilters] = useState({ boloOnly: false, roiStatus: '' })
  const [hideLowValue, setHideLowValue] = useState(true)
  const [lowValueCutoff, setLowValueCutoff] = useState(25)
  const [hideHardShip, setHideHardShip] = useState(false)
  // Closed auctions can't be bid on — hide their lots by default, but
  // keep them reachable: the enrichment work is still useful history.
  const [hideClosed, setHideClosed] = useState(true)
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

  const [lotTotal, setLotTotal] = useState(0)

  const loadLots = useCallback(() => {
    const args = {
      auctionId: selectedAuction,
      boloOnly: filters.boloOnly,
      roiStatus: filters.roiStatus || undefined,
    }
    fetchLots(args).then(setLots).catch(console.error)
    // The fetch is capped for the browser's sake; the real total comes from
    // the database so the UI never passes off a page size as the whole set.
    fetchLotCount(args).then((r) => setLotTotal(r.total)).catch(console.error)
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
  // Pull fresh auction stats and merge them into whatever is on screen, so
  // counts update after an import/enrichment without wiping scan results.
  const syncAuctionStats = useCallback(async () => {
    const fresh = await fetchAuctions()
    rememberAuctions(fresh)
    const byId = Object.fromEntries(fresh.map((a) => [a.id, a]))
    setAuctions((prev) => {
      if (!prev.length) return fresh
      return prev.map((a) => (byId[a.id] ? { ...a, ...byId[a.id] } : a))
    })
  }, [rememberAuctions])

  const refreshAll = useCallback(() => {
    syncAuctionStats().catch(console.error)
    loadLots()
  }, [loadLots, syncAuctionStats])

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
      setView('items')
      await syncAuctionStats()
      alert(`Imported ${r.created} new lots into the database`
            + (r.updated ? ` (${r.updated} already there, refreshed)` : '')
            + `.

They're listed below — use "Enrich" to price them.`)
    } catch (e) { alert(e.message); setBusy('') }
  }

  function openAuctionItems(auctionId) {
    setSelectedAuction(auctionId)
    setView('items')
  }

  async function handleEnrichAll(auctionId) {
    const a = auctions.find((x) => x.id === auctionId)
    const todo = (a?.lots_pending ?? 0) + (a?.lots_failed ?? 0)
    const hard = a?.lots_hard_pending ?? 0
    const willDo = hideHardShip ? todo - hard : todo
    const cost = (willDo * 0.005).toFixed(2)

    let msg = `Enrich ${willDo} lots from "${a?.name ?? 'this auction'}"?

`
             + `Roughly $${cost} of API usage. Progress shows in the bar at the top.`
    if (hard > 0 && !hideHardShip) {
      // Enriching a sofa costs the same as enriching a Rolex and almost never
      // pays — make that explicit before the money is spent.
      msg = `⚠ ${hard} of these ${todo} lots are HARD to ship (furniture, `
          + `appliances, pickup-only). They cost the same to enrich and rarely `
          + `clear your ROI bar.

`
          + `Tick "Hide HARD ship" in My items first and they'll be skipped.

`
          + `Enrich all ${todo} anyway? Roughly $${cost} of API usage.`
    } else if (hard > 0 && hideHardShip) {
      msg += `

Skipping ${hard} HARD-to-ship lots.`
    }
    if (!window.confirm(msg)) return

    try {
      const r = await enrichAll(auctionId, hideHardShip)
      alert(r.queued
        ? `Queued ${r.queued} lots. Progress shows in the bar at the top; each lot's status updates as it finishes.`
        : 'Nothing to enrich — every lot in this auction is already done.')
      await syncAuctionStats()
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

  // What's actually in the database for this auction, in plain words.
  function auctionState(a) {
    if (!a.lots_imported) {
      return { text: 'Not imported yet', pct: null }
    }
    const pct = Math.round((a.lots_enriched / a.lots_imported) * 100)
    const bits = [`${a.lots_imported} lots imported`]
    if (a.lots_enriched) bits.push(`${a.lots_enriched} enriched`)
    if (a.lots_inspected) bits.push(`${a.lots_inspected} inspected`)
    if (a.lots_pending) bits.push(`${a.lots_pending} not yet enriched`)
    if (a.lots_failed) bits.push(`${a.lots_failed} failed`)
    return { text: bits.join(' · '), pct }
  }

  function enrichAllLabel(a) {
    const todo = a.lots_pending + a.lots_failed
    return todo ? `Enrich ${todo}` : 'All enriched'
  }

  // An auction is "hot" when its gold-mine lots add up to real money.
  const isClosed = (a) => a.closing_date && new Date(a.closing_date) < new Date()
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
      if (hideClosed && l.auction_closed) return false
      return true
    })
    .map((l) => ({ ...l, auction_name: l.auction_name ?? auctionNames[l.auction_id] ?? '—' }))
  const hiddenCount = lots.length - visibleLots.length

  return (
    <div style={{ fontFamily: 'system-ui' }}>
      <StatusBar onQuiet={refreshAll} />
      <div style={{ padding: isMobile ? '0.75rem' : '2rem' }}>
      <h1 style={{ fontSize: isMobile ? 24 : undefined, marginBottom: 8 }}>AuctionScout</h1>

      <div style={{ display: 'flex', gap: 4, borderBottom: '1px solid var(--border)',
                    marginBottom: '1rem' }}>
        {[
          { key: 'auctions', label: `Auctions (${auctions.length})` },
          { key: 'items', label: `My items (${lotTotal || lots.length})` },
        ].map((t) => (
          <button
            key={t.key}
            onClick={() => setView(t.key)}
            style={{
              padding: isMobile ? '10px 12px' : '8px 16px',
              fontSize: isMobile ? 15 : 14,
              border: 'none', background: 'none', cursor: 'pointer',
              color: view === t.key ? 'var(--text)' : 'var(--muted)',
              fontWeight: view === t.key ? 700 : 400,
              borderBottom: view === t.key ? '2px solid var(--link)' : '2px solid transparent',
            }}
          >
            {t.label}
          </button>
        ))}
      </div>

      {view === 'auctions' && (
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
                  {isClosed(a) ? '⏹ CLOSED · ' : ''}{a.city}, {a.state} · {a.lot_count ?? '—'} lots
                  · closes {a.closing_date ? new Date(a.closing_date).toLocaleDateString() : '—'}
                  {a.buyer_premium_mult ? ` · ${Math.round((a.buyer_premium_mult - 1) * 100)}% premium` : ''}
                  {hasCategoryCount(a)
                    ? ` · ${a.category_lot_count} in ${scanCategoryName ?? 'category'}` : ''}
                </div>
                {goldBadge(a) && (
                  <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 4 }}>{goldBadge(a)}</div>
                )}
                <div style={{ fontSize: 12, color: 'var(--muted)', marginBottom: 4 }}>
                  {auctionState(a).text}
                </div>
                {auctionState(a).pct !== null && (
                  <div style={{ height: 6, borderRadius: 3, background: 'var(--badge-bg)',
                                overflow: 'hidden', marginBottom: 6 }}>
                    <div style={{ height: '100%', width: `${auctionState(a).pct}%`,
                                  background: 'var(--link)' }} />
                  </div>
                )}
                <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                  <button style={{ flex: '1 1 90px', minWidth: 90, padding: 8 }}
                          onClick={() => handleImport(a.id)} disabled={!!busy}>
                    {importLabel(a)}
                  </button>
                  {a.imported_at && (
                    <>
                      <button style={{ flex: '1 1 70px', minWidth: 70, padding: 8 }}
                              onClick={() => openAuctionItems(a.id)}>View</button>
                      <button style={{ flex: '1 1 90px', minWidth: 90, padding: 8 }}
                              disabled={!(a.lots_pending + a.lots_failed)}
                              onClick={() => handleEnrichAll(a.id)}>{enrichAllLabel(a)}</button>
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
                    <div style={{ fontSize: 11, color: 'var(--muted)' }}>{auctionState(a).text}</div>
                  </td>
                  <td style={{ paddingRight: 12 }}>
                    {isClosed(a) && <strong>⏹ CLOSED<br /></strong>}
                    {a.city}, {a.state} ({a.source})
                  </td>
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
                        <button onClick={() => openAuctionItems(a.id)}>View</button>{' '}
                        <button disabled={!(a.lots_pending + a.lots_failed)}
                                onClick={() => handleEnrichAll(a.id)}>{enrichAllLabel(a)}</button>
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
      )}

      {view === 'items' && (<>
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
        <label style={{ marginLeft: '1rem' }}>
          <input
            type="checkbox"
            checked={hideClosed}
            onChange={(ev) => setHideClosed(ev.target.checked)}
          /> Hide closed auctions
        </label>
        {(hideLowValue || hideHardShip || hideClosed) && hiddenCount > 0 && (
          <span style={{ marginLeft: '0.5rem', color: 'var(--muted)' }}>
            {hiddenCount} hidden
          </span>
        )}

        <button style={{ marginLeft: '1rem' }} onClick={loadLots}>Refresh</button>
      </section>

      <div style={{
        display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap',
        background: 'var(--highlight)', border: '1px solid var(--border)',
        borderRadius: 6, padding: '8px 10px', marginBottom: 10,
      }}>
        {selectedAuction ? (
          <>
            <span>
              Catalogue of <strong>{auctionNames[selectedAuction] ?? 'this auction'}</strong>
              {' '}— {lotTotal || lots.length} lot{(lotTotal || lots.length) === 1 ? '' : 's'} imported
            </span>
            <button onClick={() => setSelectedAuction(null)}>Show items from every auction</button>
          </>
        ) : (
          <span>
            <strong>{lotTotal.toLocaleString()} items</strong> imported across
            {' '}{new Set(lots.map((l) => l.auction_id)).size} auctions
            {lotTotal > lots.length && (
              <em style={{ color: 'var(--muted)' }}>
                {' '}— showing the first {lots.length.toLocaleString()}; open one
                auction, or filter, to narrow it down
              </em>
            )}
          </span>
        )}
        <button onClick={() => setView('auctions')} style={{ marginLeft: 'auto' }}>
          ← Back to auctions
        </button>
      </div>
      {lots.length === 0 ? (
        <p style={{ color: 'var(--muted)' }}>
          Nothing imported yet. Go to <strong>Auctions</strong>, find an auction,
          and press <strong>Import</strong> — its lots land here.
        </p>
      ) : (
        <LotTable lots={visibleLots} onLotUpdated={handleLotUpdated} onRefresh={loadLots} />
      )}
      </>)}
      </div>
    </div>
  )
}
