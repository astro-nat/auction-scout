import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { fetchLots, fetchLotCount, fetchAuctions, fetchCategories, fetchLotCategories, scanAuctions, scanGovDeals, importGovDeals, scanPublicSurplus, importPublicSurplus, scanVinted, importLots, importAllAuctions, enrichAll, enrichCategory, flushClosed, refreshBids, reinspectNoComps, fetchSettings, saveTargetRoi, savePacing, fetchWeekStats, addFavoriteHouse, removeFavoriteHouse, setAuctionHidden, fetchDismissed, undismissAuction, alertOnce, parseUtc } from './api'
import { auctionClosed, clearsFloor, underFloor, goldBadge as pacingGoldBadge } from './lib/pacing'
import { houseRatioLabel, houseRatioTitle } from './lib/calibration'
import LotTable from './components/LotTable'
import StatusBar from './components/StatusBar'
import useMediaQuery from './useMediaQuery'

const VIEW_KEY = 'auctionscout.view'
const VIEWS = ['auctions', 'items']

// The tab lives in the URL (#items), which makes it survive a refresh AND
// gives back, forward and bookmarking for free. Validated on the way in:
// anyone can type anything after the #, and an unrecognised value would
// match neither panel and render a blank page.
function viewFromHash() {
  const raw = (window.location.hash || '').replace(/^#/, '')
  return VIEWS.includes(raw) ? raw : null
}

export default function App() {
  const isMobile = useMediaQuery('(max-width: 768px)')
  // Two jobs, two screens: finding auctions vs working through what you've
  // imported. Mixing them on one page made both harder to read.
  // Which tab you were on survives a refresh. Reloading mid-way through
  // working an inventory list and landing back on Auctions means finding
  // your place again every time.
  //
  // The URL is the source of truth, so a link to #items opens there and
  // back/forward move between tabs. localStorage is the fallback for the
  // bare URL with no hash - a bookmark to the root still returns you to
  // the tab you last used. Storage access is guarded throughout because it
  // throws outright in some privacy modes rather than returning null.
  const [view, setView] = useState(() => {
    const fromHash = viewFromHash()
    if (fromHash) return fromHash
    try {
      const saved = window.localStorage.getItem(VIEW_KEY)
      return VIEWS.includes(saved) ? saved : 'auctions'
    } catch {
      return 'auctions'
    }
  })

  // First sync REPLACES the history entry; later ones push. Pushing on
  // mount would put the hash-less URL behind us, so the first Back press
  // would appear to do nothing instead of leaving the page.
  const viewSynced = useRef(false)
  useEffect(() => {
    try {
      window.localStorage.setItem(VIEW_KEY, view)
    } catch {
      // A preference that cannot be saved is not worth breaking a render.
    }
    if (viewFromHash() !== view) {
      const url = `${window.location.pathname}${window.location.search}#${view}`
      if (viewSynced.current) window.history.pushState(null, '', url)
      else window.history.replaceState(null, '', url)
    }
    viewSynced.current = true
  }, [view])

  // Back and forward change the hash without re-rendering us, so follow it.
  useEffect(() => {
    const follow = () => {
      const next = viewFromHash()
      if (next) setView(next)
    }
    window.addEventListener('hashchange', follow)
    window.addEventListener('popstate', follow)
    return () => {
      window.removeEventListener('hashchange', follow)
      window.removeEventListener('popstate', follow)
    }
  }, [])
  const [auctions, setAuctions] = useState([])
  // Which imported auctions the items view shows — an array, not one id,
  // so several can be ticked and read together. Empty = all of them.
  const [selectedAuctions, setSelectedAuctions] = useState([])
  // Items-view category filter ('' = every category), and the categories
  // actually present in the database with their enrichable counts.
  const [categoryFilter, setCategoryFilter] = useState('')
  const [lotCategories, setLotCategories] = useState([])
  const [lots, setLots] = useState([])
  const [filters, setFilters] = useState({ boloOnly: false, roiStatus: '' })
  const [hideLowValue, setHideLowValue] = useState(true)
  const [lowValueCutoff, setLowValueCutoff] = useState(25)
  // HARD-ship lots (furniture, appliances) rarely clear the ROI bar and
  // cost the most to move — hidden by default, one click to see them.
  const [hideHardShip, setHideHardShip] = useState(true)
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
  // Collapsed section headers in the auctions list. Imported starts closed:
  // that block only grows, and once it's a screen tall the scan results you
  // came here to read start below the fold. Click the header to open it.
  const [collapsedSections, setCollapsedSections] = useState(['imported'])
  const toggleSection = useCallback((key) => {
    setCollapsedSections((prev) =>
      prev.includes(key) ? prev.filter((k) => k !== key) : [...prev, key])
  }, [])
  const [scan, setScan] = useState({
    search_text: '', category_id: -1, auction_type: 'ALL',
    status: 'OPEN', zip: '', radius_miles: 25,
  })
  const [hideUnshippable, setHideUnshippable] = useState(true)
  const [showHiddenLots, setShowHiddenLots] = useState(false)
  // Show only lots marked won — the resale-inventory view. Won lots
  // are always exempt from "Hide closed": winning is what closes a lot.
  const [wonOnly, setWonOnly] = useState(false)
  const [importedRows, setImportedRows] = useState({})
  // Target ROI % for the GOLD MINE verdict — DB-backed, editable inline.
  const [targetRoi, setTargetRoi] = useState('')
  useEffect(() => {
    fetchSettings().then((s) => setTargetRoi(String(s.target_roi_pct))).catch(console.error)
  }, [])
  // Acquisition pacing: what the auction channel should contribute per week,
  // and the least an auction can put on the table before it's worth a
  // shipping minimum or a pickup trip. weekStats carries won-vs-goal.
  const [weekStats, setWeekStats] = useState(null)
  const loadWeekStats = useCallback(() => {
    fetchWeekStats().then(setWeekStats).catch(console.error)
  }, [])
  useEffect(() => { loadWeekStats() }, [loadWeekStats])
  const auctionFloor = Number(weekStats?.auction_floor_usd ?? 200)

  async function handleSavePacing(changes) {
    try {
      await savePacing(changes)
      loadWeekStats()
    } catch (e) { alertOnce(e.message) }
  }

  const [lotTotal, setLotTotal] = useState(0)
  const [lotsLoadState, setLotsLoadState] = useState('loading')
  const loadGen = useRef(0)
  // lot_id -> timestamp of the user's last enrich/inspect on it; these rows
  // are exempt from hide filters so the result can actually be read.
  const touchedRef = useRef({})
  const markTouched = useCallback((lotId) => {
    touchedRef.current[lotId] = Date.now()
  }, [])

  const loadLots = useCallback(() => {
    const args = {
      auctionIds: selectedAuctions,
      category: categoryFilter || undefined,
      boloOnly: filters.boloOnly,
      roiStatus: filters.roiStatus || undefined,
    }
    // Pages of 2000 chain automatically until the set is complete: the
    // first page renders immediately, the rest stream in behind it. One
    // 6000-row payload crashed phone tabs; four 2000-row parses don't.
    // The generation counter aborts a stale chain when the user switches
    // auction or filters mid-load. A failed page retries twice with
    // backoff (the server can be busy mid-enrichment); only then does the
    // view admit defeat — silently showing an empty list read as
    // "Nothing imported yet", which was a lie.
    const gen = ++loadGen.current
    setLotsLoadState('loading')
    const loadPage = (offset, acc, attempt = 0) =>
      fetchLots({ ...args, offset }).then((page) => {
        if (gen !== loadGen.current) return
        const all = offset ? [...acc, ...page] : page
        setLots(all)
        setLotsLoadState('ok')
        if (page.length === 2000) loadPage(all.length, all)
      }).catch((e) => {
        if (gen !== loadGen.current) return
        if (attempt < 2) {
          setTimeout(() => loadPage(offset, acc, attempt + 1), 4000 * (attempt + 1))
        } else {
          console.error(e)
          if (offset === 0) setLotsLoadState('error')
        }
      })
    loadPage(0, [])
    // The real total comes from the database so the UI never passes off a
    // page size as the whole set.
    fetchLotCount(args).then((r) => setLotTotal(r.total)).catch(console.error)
  }, [selectedAuctions, categoryFilter, filters])

  // Accumulate imported auctions as FULL rows, separately from the visible
  // list: scans replace `auctions` with whatever HiBid returned, and your
  // imported auctions must stay pinned on screen (and in the items-tab
  // dropdown) regardless. `full` marks a complete GET /auctions payload —
  // only then do we prune entries the server no longer has.
  const rememberAuctions = useCallback((list, { full = false } = {}) => {
    setAuctionIndex((prev) => {
      const next = { ...prev }
      for (const a of list) next[a.id] = a.name
      return next
    })
    setImportedRows((prev) => {
      const next = { ...prev }
      for (const a of list) if (a.lots_imported > 0) next[a.id] = a
      if (full) {
        const present = new Set(list.map((a) => a.id))
        for (const id of Object.keys(next)) {
          if (!present.has(Number(id))) delete next[id]
        }
      }
      return next
    })
    return list
  }, [])

  useEffect(() => {
    fetchAuctions()
      .then((list) => rememberAuctions(list, { full: true }))
      .then(setAuctions).catch(console.error)
  }, [rememberAuctions])
  useEffect(() => { fetchCategories().then(setCategories).catch(console.error) }, [])
  const loadLotCategories = useCallback(() => {
    fetchLotCategories().then(setLotCategories).catch(console.error)
  }, [])
  useEffect(() => { loadLotCategories() }, [loadLotCategories])
  useEffect(() => { loadLots() }, [loadLots])

  const scanIsAnywhere = Number(scan.radius_miles) === -1

  async function handleScan() {
    setBusy('Scanning HiBid…')
    try {
      const found = await scanAuctions({
        ...scan,
        zip: scan.zip || undefined,
        category_id: Number(scan.category_id),
        radius_miles: Number(scan.radius_miles),
        // "Anywhere" without a status limit returns every auction on HiBid —
        // keep it bounded to auctions closing soon instead.
        status: scanIsAnywhere ? 'CLOSING' : scan.status,
      })
      rememberAuctions(found)
      setAuctions(found)
      setAuctionLimit(50)
    } catch (e) { alertOnce(e.message) } finally { setBusy('') }
  }

  async function handleScanGovDeals() {
    setBusy('Scanning GovDeals…')
    try {
      const found = await scanGovDeals({
        zip: scan.zip || undefined,
        // GovDeals has no "Anywhere" — pickup-only inventory outside
        // driving range is inventory you can never collect.
        radius_miles: scanIsAnywhere ? 100 : Number(scan.radius_miles),
      })
      rememberAuctions(found)
      setAuctions(found)
      setAuctionLimit(50)
    } catch (e) { alertOnce(e.message) } finally { setBusy('') }
  }

  async function handleScanPublicSurplus() {
    setBusy('Scanning PublicSurplus…')
    try {
      const found = await scanPublicSurplus({
        zip: scan.zip || undefined,
        // Pickup-only, same as GovDeals: "Anywhere" is capped.
        radius_miles: scanIsAnywhere ? 100 : Number(scan.radius_miles),
      })
      rememberAuctions(found)
      setAuctions(found)
      setAuctionLimit(50)
    } catch (e) { alertOnce(e.message) } finally { setBusy('') }
  }

  async function handleScanVinted() {
    const query = scan.search_text.trim()
    if (!query) {
      alert('Type what to watch in the keyword box first — Vinted scans a '
            + 'search ("pyrex", "coach bag"), not an area.')
      return
    }
    setBusy(`Scanning Vinted for "${query}"…`)
    try {
      const found = await scanVinted(query)
      rememberAuctions(found)
      setAuctions(found)
      setAuctionLimit(50)
    } catch (e) { alertOnce(e.message) } finally { setBusy('') }
  }

  // Called when the status bar sees the server go idle — pull fresh data so
  // finished imports/enrichments appear without a manual refresh.
  // Pull fresh auction stats and merge them into whatever is on screen, so
  // counts update after an import/enrichment without wiping scan results.
  const syncAuctionStats = useCallback(async () => {
    const fresh = await fetchAuctions()
    rememberAuctions(fresh, { full: true })
    const byId = Object.fromEntries(fresh.map((a) => [a.id, a]))
    setAuctions((prev) => {
      if (!prev.length) return fresh
      // Update rows in place AND drop the ones the server no longer lists —
      // an auction missing from the full listing has closed (or been
      // purged), and keeping it made the list fill with dead auctions the
      // longer the tab stayed open.
      return prev.filter((a) => byId[a.id]).map((a) => ({ ...a, ...byId[a.id] }))
    })
  }, [rememberAuctions])

  const refreshAll = useCallback(() => {
    syncAuctionStats().catch(console.error)
    loadLotCategories()
    loadLots()
    loadWeekStats()
  }, [loadLots, loadLotCategories, syncAuctionStats, loadWeekStats])

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

  // categoryId defaults to the last scan's category (the auctions-tab flow);
  // pass -1 to import the full catalog regardless — the items-tab "import
  // the rest" button must not silently inherit a stale category filter.
  // Imports run on the worker now: holding the request open while HiBid
  // paged through a 1,200-lot catalog was a timeout with a progress bar,
  // and closing the tab cancelled the import mid-save.
  async function handleImport(auctionId, categoryId = scanCategoryId) {
    // GovDeals / PublicSurplus import through their own endpoints:
    // synchronous and small (one search response), lots there on the spot.
    const target = auctions.find((a) => a.id === auctionId)
    const platformImport = target?.external_id?.startsWith('gd-') ? importGovDeals
      : target?.external_id?.startsWith('ps-') ? importPublicSurplus
      // A Vinted card's "import" is just its scan run again: same query,
      // fresh prices, sold items closed out.
      : target?.external_id?.startsWith('vt-')
        ? () => scanVinted(target.name.replace(/^Vinted: /, '')).then((cards) => {
            const c = cards.find((x) => x.id === auctionId)
            return { created: 0, updated: c?.lot_count ?? 0 }
          })
      : null
    if (platformImport) {
      try {
        const r = await platformImport(auctionId)
        setSelectedAuctions([auctionId])
        setView('items')
        alert(`Imported ${r.created + r.updated} lots `
              + `(${r.created} new). Price them from the inventory view.`)
        refreshAll()
      } catch (e) { alertOnce(e.message) }
      return
    }
    try {
      const r = await importLots(auctionId, categoryId)
      if (r.already_running) {
        alert('An import is already running — let it finish first. Progress shows in the top bar.')
        return
      }
      setSelectedAuctions([auctionId])
      setView('items')
      alert(`Importing in the background — progress shows in the top bar, `
            + `and the items appear here when it finishes.`)
    } catch (e) { alertOnce(e.message) }
  }

  async function handleSaveRoi() {
    const pct = Number(targetRoi)
    if (!pct || pct < 1) { alert('Enter a target ROI percent, e.g. 150.'); return }
    try {
      const r = await saveTargetRoi(pct)
      alert(`Target ROI set to ${r.target_roi_pct}%. Re-grading ${r.regrading} items — takes a few seconds.`)
    } catch (e) { alertOnce(e.message) }
  }

  async function handleRefreshBids() {
    // No popup on success — the status bar showing the job IS the feedback,
    // and this runs often (button + hourly timer).
    try {
      // Fully silent: the status bar is the feedback, and "nothing to
      // refresh" is not worth interrupting for either.
      await refreshBids()
    } catch (e) { alertOnce(e.message) }
  }

  async function handleInspectNoValue() {
    try {
      const peek = await reinspectNoComps(true)
      if (!peek.lots) { alert('Every enriched item in an open auction already has a value.'); return }
      const cost = (peek.lots * 0.01).toFixed(2)
      const msg = `Price ${peek.lots} items individually — the ones with no value yet?\n\n`
        + `AI reads each one's full-size photo, identifies the items, and prices `
        + `them (real comps first, its own estimate as fallback). Roughly $${cost} `
        + `of API usage. Progress shows in the bar at the top.`
      if (!window.confirm(msg)) return
      const r = await reinspectNoComps()
      alert(`Queued ${r.queued} items for inspection.`)
    } catch (e) { alertOnce(e.message) }
  }

  async function handleFlushClosed() {
    try {
      const peek = await flushClosed(true)
      if (!peek.lots) { alert('No items from closed auctions to flush.'); return }
      const msg = `Permanently delete ${peek.lots} items from closed auctions?\n\n`
        + `Their enrichment results (the AI calls you paid for) are deleted `
        + `with them. This can't be undone.\n\n`
        + `Lots marked watched are kept, and lots marked won are `
        + `kept for 7 days after marking — mark anything you won before `
        + `flushing.`
      if (!window.confirm(msg)) return
      setBusy('Flushing closed items…')
      const r = await flushClosed()
      setBusy('')
      alert(`Flushed ${r.lots} items`
            + (r.auctions ? ` and removed ${r.auctions} empty closed auctions` : '')
            + '.')
      refreshAll()
    } catch (e) { alertOnce(e.message); setBusy('') }
  }

  function openAuctionItems(auctionId) {
    setSelectedAuctions([auctionId])
    setView('items')
  }

  const toggleAuctionSelected = useCallback((id) => {
    setSelectedAuctions((prev) =>
      prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id])
  }, [])

  async function handleEnrichCategory() {
    try {
      // Dry-run first: the stored counts can be stale, and the confirm
      // dialog should quote the number that will actually be spent on.
      const peek = await enrichCategory(categoryFilter, { skipHard: hideHardShip, dryRun: true })
      if (!peek.lots) {
        alert(`Every "${categoryFilter}" item in an open auction is already enriched`
              + (hideHardShip ? ' (HARD-to-ship items are being skipped).' : '.'))
        return
      }
      const cost = (peek.lots * 0.005).toFixed(2)
      let msg = `Price ${peek.lots} "${categoryFilter}" items across ALL imported auctions?\n\n`
              + `Roughly $${cost} of API usage. Progress shows in the bar at the top.`
      if (hideHardShip) msg += `\n\nSkipping HARD-to-ship items ("Hide HARD ship" is on).`
      if (!window.confirm(msg)) return
      const r = await enrichCategory(categoryFilter, { skipHard: hideHardShip })
      alert(`Queued ${r.queued} items. Each one's status updates as it finishes.`)
      loadLots()
    } catch (e) { alertOnce(e.message) }
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
      // Pricing a sofa costs the same as pricing a Rolex and almost never
      // pays — make that explicit before the money is spent.
      msg = `Warning: ${hard} of these ${todo} lots are HARD to ship (furniture, `
          + `appliances, pickup-only). They cost the same to enrich and rarely `
          + `clear your ROI bar.

`
          + `Tick "Hide HARD ship" in My inventory first and they'll be skipped.

`
          + `Price all ${todo} anyway? Roughly $${cost} of API usage.`
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
    } catch (e) { alertOnce(e.message) }
  }

  function handleLotUpdated(updated) {
    setLots((prev) => {
      const i = prev.findIndex((l) => l.lot_id === updated.lot_id)
      if (i === -1) return prev
      // A poll tick that changed nothing must not change state: replacing
      // the array re-copies and re-sorts every lot on screen, and during a
      // long inspection those no-op ticks arrive for minutes on end.
      if (JSON.stringify(prev[i]) === JSON.stringify(updated)) return prev
      const next = [...prev]
      next[i] = updated
      return next
    })
  }

  // "Confirmed low-value" = 3+ comps agree the resale is under the cutoff.
  // Unenriched lots stay visible — unknown is not the same as confirmed cheap.
  // "Confirmed" low-value = the price is trustworthy AND under the cutoff:
  // either 3+ real comps agree, or the AI identified the item with strong
  // confidence (it knows exactly what it's pricing, even off fewer comps or
  // its own estimate). Weakly-identified lots stay visible — an uncertain
  // cheap guess isn't proof of a cheap item.
  function isConfirmedLowValue(lot) {
    const e = lot.enrichment
    if (e?.est_resale == null || Number(e.est_resale) >= lowValueCutoff) return false
    return e.comp_count >= 3 || e.confidence === 'strong'
  }

  // What's actually in the database for this auction, in plain words.
  function auctionState(a) {
    if (!a.lots_imported) {
      return { text: 'Not imported yet', pct: null }
    }
    const pct = Math.round((a.lots_enriched / a.lots_imported) * 100)
    // "X of Y imported" whenever HiBid's catalog size is known — a partial
    // import reads as complete otherwise, and nobody notices the gap.
    const bits = [a.lot_count != null
      ? `${a.lots_imported} of ${a.lot_count} imported`
      : `${a.lots_imported} lots imported`]
    if (a.lots_enriched) bits.push(`${a.lots_enriched} enriched`)
    if (a.lots_inspected) bits.push(`${a.lots_inspected} inspected`)
    if (a.lots_pending) bits.push(`${a.lots_pending} not yet enriched`)
    if (a.lots_failed) bits.push(`${a.lots_failed} failed`)
    return { text: bits.join(' · '), pct }
  }

  function enrichAllLabel(a) {
    const todo = a.lots_pending + a.lots_failed
    return todo ? `Price ${todo}` : 'All priced'
  }

  // AI-read shipping estimate, as a compact row tag. Tooltip carries the
  // full policy sentence.
  function shipBadge(a) {
    if (a.ship_cost_estimate != null) {
      return { text: `~$${Math.round(a.ship_cost_estimate)}/item ship`, tip: a.ship_summary }
    }
    if (a.ship_summary) {
      const noShip = /pickup only|no shipping/i.test(a.ship_summary)
      return { text: noShip ? 'no ship' : 'ship: see terms', tip: a.ship_summary }
    }
    return null
  }

  // Shipping analysis said "no shipping" — worthless unless it's local
  // enough to pick up (source tells us which scan geography found it).
  const isUnshippable = (a) =>
    a.ship_summary && a.ship_cost_estimate == null
    && /pickup only|no shipping/i.test(a.ship_summary)
    && a.source !== 'Local Pickup'

  // What the auction actually does, not which scan found it. `source` only
  // records scan geography ("Local Pickup" = inside your radius), so a
  // Houston auction that's actually ship-only was labeled "(Local Pickup)".
  // Once the AI has read the terms, its answer wins.
  const fulfillment = (a) => {
    if (a.ship_cost_estimate != null) return 'Ships'
    if (a.ship_summary && /pickup only|no shipping/i.test(a.ship_summary)) return 'Pickup only'
    return a.source
  }

  const visibleAuctions = hideUnshippable
    ? auctions.filter((a) => !isUnshippable(a))
    : auctions

  // "Import all" targets: every auction on screen that's still open and —
  // when the scan had a category — isn't already known to have zero
  // matching lots (importing those would just burn time on empty fetches).
  const importAllCandidates = visibleAuctions.filter((a) =>
    !(a.closing_date && parseUtc(a.closing_date) < new Date())
    && !(hasCategoryCount(a) && a.category_lot_count === 0)
    // GovDeals/PublicSurplus import one at a time through their own
    // endpoints — the HiBid bulk job would silently skip them anyway.
    && !a.external_id)

  async function handleImportAll(boloOnly = false) {
    const ids = importAllCandidates.map((a) => a.id)
    if (!ids.length) return
    const inCategory = scanCategoryId !== -1 && scanCategoryName
    // The two filters compose, so name whichever are actually active
    // rather than pretending there are three fixed choices.
    const what = boloOnly
      ? (inCategory
        ? `only their "${scanCategoryName}" lots that match your BOLO list`
        : 'only lots matching your BOLO list')
      : (inCategory ? `only their "${scanCategoryName}" lots`
                    : 'all their open lots')
    const msg = `Import from all ${ids.length} listed auctions (${what})?\n\n`
      + (boloOnly
        ? `Lots that do not match a BOLO brand are skipped and never `
          + `saved. The matching is free, so this costs the same as a `
          + `full import and simply keeps fewer rows.` + String.fromCharCode(10, 10)
        : '')
      + `Free — no AI calls. Runs in the background: progress shows in the `
      + `bar at the top, and imported lots appear under "My inventory" as each `
      + `auction finishes.`
    if (!window.confirm(msg)) return
    try {
      const r = await importAllAuctions(ids, scanCategoryId, boloOnly)
      if (r.already_running) {
        alert('A bulk import is already running — check the bar at the top.')
        return
      }
      alert(`Queued ${r.auctions} auctions for import.`)
    } catch (e) { alertOnce(e.message) }
  }

  // Watch / unwatch an auction house. Optimistic: the star flips at once and
  // reverts if the call fails, because the only visible effect otherwise is a
  // re-sort that looks like nothing happened.
  const toggleFavorite = useCallback(async (auction) => {
    if (!auction.auctioneer_id) return
    const next = !auction.favorite
    const apply = (fav) => setAuctions((prev) => prev.map((a) =>
      a.auctioneer_id === auction.auctioneer_id ? { ...a, favorite: fav } : a))
    apply(next)
    try {
      if (next) {
        await addFavoriteHouse({ auctionId: auction.id, name: auction.auctioneer })
      } else {
        await removeFavoriteHouse(auction.auctioneer_id)
      }
    } catch (err) {
      apply(!next)
      alertOnce(`Could not ${next ? 'watch' : 'unwatch'} that house: ${err.message}`)
    }
  }, [])

  // Forget an auction. Removed from the list immediately rather than waiting
  // for a refetch — the point of the button is that it goes away. The backend
  // also records the HiBid event id so future scans skip it entirely.
  const hideAuction = useCallback(async (auction) => {
    setAuctions((prev) => prev.filter((a) => a.id !== auction.id))
    setImportedRows((prev) => {
      const next = { ...prev }
      delete next[auction.id]
      return next
    })
    try {
      await setAuctionHidden(auction.id, true)
      setDismissed((prev) => [{ hibid_id: auction.hibid_id, name: auction.name }, ...prev])
    } catch (err) {
      setAuctions((prev) => [...prev, auction])
      alertOnce(`Could not forget that auction: ${err.message}`)
    }
  }, [])

  // Forgotten auctions, for the restore list. Scans skip them, so without
  // this panel there'd be no way back from a mis-click.
  const [dismissedList, setDismissed] = useState([])
  const loadDismissed = useCallback(() => {
    fetchDismissed().then(setDismissed).catch(console.error)
  }, [])
  useEffect(() => { loadDismissed() }, [loadDismissed])

  // Forgetting is permanent and scan-proof, so it asks first — but only when
  // there are imported lots at stake, since the card carries your enrichment
  // work. A plain scan result goes on one click, as before.
  function confirmForget(auction) {
    if (auction.lots_imported > 0) {
      const msg = `Forget "${auction.name}"?\n\n`
        + `It won't appear in future scans. Your ${auction.lots_imported} `
        + `imported lots stay in My inventory — only the auction card goes away.\n\n`
        + `Undo it any time from "Forgotten" under the scan button.`
      if (!window.confirm(msg)) return
    }
    hideAuction(auction)
  }

  const restoreAuction = useCallback(async (hibidId) => {
    setDismissed((prev) => prev.filter((d) => d.hibid_id !== hibidId))
    try {
      await undismissAuction(hibidId)
    } catch (err) {
      loadDismissed()
      alertOnce(`Could not restore that auction: ${err.message}`)
    }
  }, [loadDismissed])

  // Imported auctions stay pinned at the top from their own accumulated
  // store — a scan replacing the visible list can't knock them off screen.
  // The no-ship filter only applies to discovered auctions; imported ones
  // are yours either way.
  const importedAuctions = Object.values(importedRows)
    .sort((a, b) => (parseUtc(a.closing_date) ?? new Date('9999-01-01')) - (parseUtc(b.closing_date) ?? new Date('9999-01-01')))
  const importedIds = new Set(Object.keys(importedRows).map(Number))
  const notImported = visibleAuctions.filter((a) => !importedIds.has(a.id))
  // Watched auction houses sit above the rest of the discovered list: a house
  // you've starred is one you already trust, so its sales are worth seeing
  // before a stranger's that happens to close sooner.
  const watchedAuctions = notImported.filter((a) => a.favorite)
  const discoveredAuctions = notImported.filter((a) => !a.favorite)
  const auctionSections = [
    ...(importedAuctions.length ? [{ key: 'imported', label: `Imported (${importedAuctions.length})`, rows: importedAuctions }] : []),
    ...(watchedAuctions.length ? [{ key: 'watched', label: `★ Watched houses (${watchedAuctions.length})`, rows: watchedAuctions }] : []),
    ...(discoveredAuctions.length ? [{ key: 'discovered', label: (importedAuctions.length || watchedAuctions.length) ? `Discovered (${discoveredAuctions.length})` : null, rows: discoveredAuctions }] : []),
  ]
  // A section only collapses when it has a header to click — the lone
  // Discovered section renders headerless, and hiding it would strand the
  // rows with no way to get them back.
  const auctionRowsForDisplay = auctionSections.flatMap((s) => {
    const collapsed = !!s.label && collapsedSections.includes(s.key)
    return [
      ...(s.label ? [{ header: s.label, sectionKey: s.key, collapsed }] : []),
      ...(collapsed ? [] : s.rows),
    ]
  })

  // Floor verdicts live in lib/pacing.js (pure, unit-tested); these wrappers
  // just bind the user's configured floor.
  const isClosed = (a) => auctionClosed(a)
  const isHotAuction = (a) => clearsFloor(a, auctionFloor)
  const isUnderFloor = (a) => underFloor(a, auctionFloor)
  const goldBadge = (a) => pacingGoldBadge(a, auctionFloor)

  // Stamp each lot with its auction's name so the table can show/filter it.
  // Memoized: with several thousand lots streamed in, rebuilding this array
  // of copies on every unrelated render is real work on a phone.
  // Closed at the ITEM level: the whole auction ended, or this lot's own
  // HiBid status says it's done — catalogs soft-close progressively, so an
  // open auction can be full of already-closed lots.
  const isClosedItem = (l) =>
    l.auction_closed || /^(closed|sold|ended|passed|archived)/i.test(l.status || '')

  const visibleLots = useMemo(() => {
    const auctionNames = { ...auctionIndex, ...Object.fromEntries(auctions.map((a) => [a.id, a.name])) }
    return lots
      .filter((l) => {
        if (wonOnly && !l.won) return false
        if (!showHiddenLots && l.hidden) return false
        // Won lots ignore the hide rules below — they're inventory now, and
        // the enrichment is what gets them listed on eBay.
        if (l.won) return true
        // Lots the user just enriched/inspected are exempt from the hide
        // rules for a while — a fresh result that instantly trips a filter
        // vanishes before it can be read. (The manual hide still wins.)
        if (touchedRef.current[l.lot_id] &&
            Date.now() - touchedRef.current[l.lot_id] < 15 * 60 * 1000) return true
        if (hideLowValue && isConfirmedLowValue(l)) return false
        if (hideHardShip && l.logistics_ease === 'HARD') return false
        if (hideClosed && isClosedItem(l)) return false
        return true
      })
      .map((l) => ({ ...l,
                     auction_name: l.auction_name ?? auctionNames[l.auction_id] ?? '—',
                     item_closed: isClosedItem(l) }))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lots, auctions, auctionIndex, showHiddenLots, wonOnly, hideLowValue, lowValueCutoff, hideHardShip, hideClosed])
  const hiddenCount = lots.length - visibleLots.length

  return (
    <div style={{ fontFamily: 'system-ui' }}>
      <StatusBar onQuiet={refreshAll} />
      <div style={{ padding: isMobile ? '0.75rem' : '1.5rem 2rem',
                    maxWidth: 1500, margin: '0 auto' }}>
      <h1 style={{ fontSize: isMobile ? 22 : 26, margin: '0 0 2px',
                   display: 'flex', alignItems: 'baseline', gap: 10 }}>
        AuctionScout
        {!isMobile && (
          <span style={{ fontSize: 13, fontWeight: 400, color: 'var(--muted)',
                         letterSpacing: 0 }}>
            find it cheap, flip it well
          </span>
        )}
      </h1>

      <div style={{ display: 'flex', gap: 4, borderBottom: '1px solid var(--border)',
                    marginBottom: '1rem' }}>
        {[
          { key: 'auctions', label: `Auctions (${auctions.length})` },
          { key: 'items', label: `My inventory (${lotTotal || lots.length})` },
        ].map((t) => (
          <button
            key={t.key}
            className={`tab${view === t.key ? ' active' : ''}`}
            onClick={() => setView(t.key)}
            style={isMobile ? { fontSize: 15, padding: '10px 12px' } : undefined}
          >
            {t.label}
          </button>
        ))}
      </div>

      {view === 'auctions' && (
      <section style={{ marginBottom: '1.5rem' }}>
        {/* Acquisition pacing: is this week on track, and how much trusted
            profit is still on the board. Wins enter via the Won mark. */}
        {weekStats && (() => {
          const goal = Number(weekStats.weekly_goal_usd)
          const won = Number(weekStats.won_trusted_profit)
          const avail = Number(weekStats.available_gold_profit)
          const pct = goal > 0 ? Math.max(0, Math.min(100, (won / goal) * 100)) : 0
          return (
            <div className="card" style={{ marginBottom: 10, padding: '8px 12px' }}>
              <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'baseline',
                            gap: '4px 14px', fontSize: 14 }}>
                <strong>This week: ${won.toFixed(0)} of $
                  <input
                    value={weekStats.weekly_goal_usd}
                    onChange={(ev) => setWeekStats((s) => ({ ...s, weekly_goal_usd: ev.target.value }))}
                    onBlur={(ev) => { const v = Number(ev.target.value); if (v >= 0) handleSavePacing({ weekly_goal_usd: v }) }}
                    title="Weekly guaranteed-profit goal for the auction channel"
                    style={{ width: 52, fontSize: 14, fontWeight: 700, padding: '0 2px' }}
                  /> goal</strong>
                <span style={{ color: 'var(--muted)' }}>
                  {weekStats.won_count ? `${weekStats.won_count} marked won` : 'nothing marked won yet'}
                  {' · '}~${avail.toFixed(0)} still on the board
                </span>
                <label style={{ marginLeft: 'auto', color: 'var(--muted)', fontSize: 13,
                                whiteSpace: 'nowrap' }}
                       title="An auction must put at least this much audit-trusted profit on the table to be worth a shipping minimum or a pickup trip">
                  floor $
                  <input
                    value={weekStats.auction_floor_usd}
                    onChange={(ev) => setWeekStats((s) => ({ ...s, auction_floor_usd: ev.target.value }))}
                    onBlur={(ev) => { const v = Number(ev.target.value); if (v >= 0) handleSavePacing({ auction_floor_usd: v }) }}
                    style={{ width: 44, fontSize: 13, padding: '0 2px' }}
                  />/auction
                </label>
              </div>
              <div style={{ height: 6, borderRadius: 3, background: 'var(--badge-bg)',
                            overflow: 'hidden', marginTop: 6 }}>
                <div style={{ height: '100%', width: `${pct}%`, background: 'var(--link)' }} />
              </div>
            </div>
          )
        })()}
        {/* Form wrapper: pressing Enter in any filter field runs the scan */}
        <form onSubmit={(ev) => { ev.preventDefault(); handleScan() }}
              style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        {/* Row 1: what to look for */}
        <input
          value={scan.search_text}
          onChange={(ev) => setScanField('search_text', ev.target.value)}
          placeholder="Keyword (auction name/content)…"
          style={{ padding: 8, fontSize: 14, width: '100%', maxWidth: isMobile ? '100%' : 480,
                   boxSizing: 'border-box' }}
        />
        {/* Row 2: narrowing filters, evenly gapped */}
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
          <select value={scan.category_id} onChange={(ev) => setScanField('category_id', ev.target.value)}
                  style={{ flex: isMobile ? '1 1 45%' : '0 1 auto', padding: 6, fontSize: 14, minWidth: 140 }}>
            <option value={-1}>All categories</option>
            {categories.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
          <select value={scan.auction_type} onChange={(ev) => setScanField('auction_type', ev.target.value)}
                  style={{ flex: isMobile ? '1 1 45%' : '0 1 auto', padding: 6, fontSize: 14, minWidth: 130 }}>
            <option value="ALL">All auction types</option>
            <option value="ONLINE">Online Only</option>
            <option value="WEBCAST">Live Webcast</option>
            <option value="ABSENTEE">Absentee</option>
            <option value="LISTING">Listing Only</option>
          </select>
          <select value={scanIsAnywhere ? 'CLOSING' : scan.status}
                  onChange={(ev) => setScanField('status', ev.target.value)}
                  disabled={scanIsAnywhere}
                  title={scanIsAnywhere ? 'Anywhere is locked to "Closing soon" so it can\'t return every auction on HiBid' : undefined}
                  style={{ flex: isMobile ? '1 1 45%' : '0 1 auto', padding: 6, fontSize: 14, minWidth: 110 }}>
            <option value="OPEN">Open</option>
            <option value="CLOSING">Closing soon</option>
            <option value="HOT">Hot</option>
            <option value="ALL">Any status</option>
          </select>
          <input
            value={scan.zip}
            onChange={(ev) => setScanField('zip', ev.target.value)}
            placeholder="Zip (77058)"
            style={{ flex: isMobile ? '1 1 45%' : '0 1 auto', padding: 6, fontSize: 14,
                     minWidth: 90, maxWidth: 130, boxSizing: 'border-box' }}
          />
          <select value={scan.radius_miles} onChange={(ev) => setScanField('radius_miles', ev.target.value)}
                  style={{ flex: isMobile ? '1 1 45%' : '0 1 auto', padding: 6, fontSize: 14, minWidth: 100 }}>
            <option value={5}>5 miles</option>
            <option value={25}>25 miles</option>
            <option value={50}>50 miles</option>
            <option value={100}>100 miles</option>
            <option value={250}>250 miles</option>
            <option value={500}>500 miles</option>
            <option value={-1}>Anywhere</option>
          </select>
        </div>
        {/* Row 3: go + the results-shaping toggle */}
        <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 12 }}>
          <button type="submit" disabled={!!busy} className="primary"
                  style={isMobile ? { flex: '1 1 100%', padding: 10, fontSize: 15 }
                                  : { padding: '8px 18px' }}>
            Scan auctions
          </button>
          <button type="button" onClick={handleScanGovDeals} disabled={!!busy}
                  title="Search GovDeals (government surplus) near your zip — one card per selling agency"
                  style={isMobile ? { flex: '1 1 100%', padding: 10, fontSize: 15 }
                                  : { padding: '8px 18px' }}>
            Scan GovDeals
          </button>
          <button type="button" onClick={handleScanPublicSurplus} disabled={!!busy}
                  title="Search PublicSurplus (school & city surplus) near your zip — one card for the whole area"
                  style={isMobile ? { flex: '1 1 100%', padding: 10, fontSize: 15 }
                                  : { padding: '8px 18px' }}>
            Scan PublicSurplus
          </button>
          <button type="button" onClick={handleScanVinted} disabled={!!busy}
                  title="Watch a Vinted search (uses the keyword box) — newest listings graded at their asking price; rescan to refresh"
                  style={isMobile ? { flex: '1 1 100%', padding: 10, fontSize: 15 }
                                  : { padding: '8px 18px' }}>
            Scan Vinted
          </button>
          {importAllCandidates.length > 1 && (
            <button type="button" onClick={handleImportAll} disabled={!!busy}
                    title={scanCategoryId !== -1 && scanCategoryName
                      ? `Import the matching "${scanCategoryName}" lots from every open auction listed below — one background job`
                      : 'Import all open lots from every auction listed below — one background job'}
                    style={isMobile ? { flex: '1 1 100%', padding: 10, fontSize: 15 }
                                    : { padding: '8px 18px' }}>
              Import all ({importAllCandidates.length}
              {scanCategoryId !== -1 && scanCategoryName ? ` · ${scanCategoryName}` : ''})
            </button>
          )}
          {importAllCandidates.length > 1 && (
            <button type="button" onClick={() => handleImportAll(true)} disabled={!!busy}
                    title="Import only lots whose title matches your BOLO brand list. The match is free regex over the title the fetch already returned, so this costs the same as a full import and simply keeps fewer rows."
                    style={isMobile ? { flex: '1 1 100%', padding: 10, fontSize: 15 }
                                    : { padding: '8px 18px' }}>
              Import BOLO matches ({importAllCandidates.length}
              {scanCategoryId !== -1 && scanCategoryName ? ` · ${scanCategoryName}` : ''})
            </button>
          )}
          {busy && <span>{busy}</span>}
          <label style={{ display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: 13, whiteSpace: 'nowrap' }}
                 title="Shipping analysis found these don't ship, and they're outside your pickup radius — nothing you could actually buy">
            <input
              type="checkbox"
              checked={hideUnshippable}
              onChange={(ev) => setHideUnshippable(ev.target.checked)}
            /> Hide no-ship outside my radius
            {hideUnshippable && auctions.length - visibleAuctions.length > 0 && (
              <span style={{ color: 'var(--muted)' }}>
                ({auctions.length - visibleAuctions.length} hidden)
              </span>
            )}
          </label>
          {dismissedList.length > 0 && (
            <details className="picker" style={{ position: 'relative' }}>
              <summary style={{ fontSize: 13 }}
                       title="Auctions you've forgotten. Scans skip these — restore one to see it again.">
                Forgotten ({dismissedList.length}) ▾
              </summary>
              <div className="panel">
                {dismissedList.map((d) => (
                  <div key={d.hibid_id}
                       style={{ display: 'flex', gap: 6, alignItems: 'center',
                                padding: '6px 4px', fontSize: 13 }}>
                    <span style={{ lineHeight: 1.3, flex: 1 }}>
                      {d.name || `HiBid auction ${d.hibid_id}`}
                    </span>
                    {/* type=button: this panel lives inside the scan form,
                        and a default submit button fires a HiBid scan. */}
                    <button type="button"
                            onClick={() => restoreAuction(d.hibid_id)}
                            title="Show this auction again in future scans"
                            style={{ padding: '2px 8px', fontSize: 12 }}>
                      Restore
                    </button>
                  </div>
                ))}
              </div>
            </details>
          )}
        </div>
        </form>
        {(importedAuctions.length + discoveredAuctions.length) === 0 && !busy && (
          <div className="empty-state" style={{ marginTop: '0.75rem' }}>
            <div><strong>No auctions on screen yet.</strong></div>
            <div style={{ marginTop: 4 }}>
              Pick a category or radius above and press <strong>Scan auctions</strong> to
              see what's closing near you.
            </div>
          </div>
        )}
        {(importedAuctions.length + discoveredAuctions.length) > 0 && (isMobile ? (
          <details style={{ marginTop: '0.75rem' }} open={!selectedAuctions.length}>
            <summary style={{ fontWeight: 600, padding: '4px 0' }}>
              Auctions ({importedAuctions.length + discoveredAuctions.length})
            </summary>
            {auctionRowsForDisplay.slice(0, auctionLimit).map((a) => a.header ? (
              <div key={`hdr-${a.header}`} style={{ marginTop: 12 }}>
                <button className="bare"
                        onClick={() => toggleSection(a.sectionKey)}
                        aria-expanded={!a.collapsed}
                        title={a.collapsed ? 'Show these auctions' : 'Hide these auctions'}
                        style={{ fontWeight: 700, fontSize: 15, padding: 0 }}>
                  {a.collapsed ? '▸' : '▾'} {a.header}
                </button>
              </div>
            ) : (
              <div key={a.id}
                   className={`card${isHotAuction(a) ? ' row-gold' : ''}`}
                   style={{ marginTop: 8,
                            opacity: isUnderFloor(a) ? 0.55 : undefined,
                            background: !isHotAuction(a) && selectedAuctions.includes(a.id)
                              ? 'var(--highlight)' : undefined }}>
                <div style={{ fontWeight: 600, display: 'flex', alignItems: 'center', gap: 6 }}>
                  <button
                    className="bare"
                    onClick={() => toggleFavorite(a)}
                    disabled={!a.auctioneer_id}
                    title={!a.auctioneer_id
                      ? 'Auction house unknown — re-scan to pick it up'
                      : a.favorite
                        ? `Unwatch ${a.auctioneer || 'this house'}`
                        : `Watch ${a.auctioneer || 'this house'} — its sales sort to the top`}
                    style={{
                      fontSize: 15, lineHeight: 1,
                      cursor: a.auctioneer_id ? 'pointer' : 'default',
                      opacity: a.auctioneer_id ? 1 : 0.3,
                      filter: a.favorite ? 'none' : 'grayscale(1)',
                    }}>
                    {a.favorite ? '★' : '☆'}
                  </button>
                  <a href={a.source_url} target="_blank" rel="noreferrer">{a.name}</a>
                  <button
                    className="bare"
                    onClick={() => confirmForget(a)}
                    title="Forget this auction — it won't come back in future scans"
                    style={{
                      marginLeft: 'auto', padding: '0 2px', fontSize: 14,
                      lineHeight: 1, color: 'var(--muted)',
                    }}>
                    ✕
                  </button>
                </div>
                <div style={{ fontSize: 13, color: 'var(--muted)', margin: '4px 0' }}>
                  {isClosed(a) ? 'CLOSED · ' : ''}{a.city}, {a.state} · {a.lot_count ?? '—'} lots
                  · closes {a.closing_date ? parseUtc(a.closing_date).toLocaleDateString() : '—'}
                  {a.buyer_premium_mult ? ` · ${Math.round((a.buyer_premium_mult - 1) * 100)}% premium` : ''}
                  {houseRatioLabel(a.estimate_ratio, a.estimate_ratio_n) && (
                    <span title={houseRatioTitle(a.estimate_ratio, a.estimate_ratio_n)}
                          style={{ cursor: 'help' }}>
                      {' · '}{houseRatioLabel(a.estimate_ratio, a.estimate_ratio_n)}
                    </span>
                  )}
                  {hasCategoryCount(a)
                    ? ` · ${a.category_lot_count} in ${scanCategoryName ?? 'category'}` : ''}
                  {shipBadge(a) ? ` · ${shipBadge(a).text}` : ''}
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
            {auctionRowsForDisplay.length > auctionLimit && (
              <button style={{ width: '100%', padding: 8, marginTop: 8 }}
                      onClick={() => setAuctionLimit((n) => n + 50)}>
                Show more auctions ({auctionRowsForDisplay.length - auctionLimit} more)
              </button>
            )}
          </details>
        ) : (
          <table className="data-table" style={{ marginTop: '0.75rem' }}>
            <thead style={{
              position: 'sticky', top: 'var(--statusbar-h, 0px)', zIndex: 10,
              background: 'var(--card-bg)',
            }}>
              <tr>
                <th>Auction</th>
                <th>Where</th>
                <th className="num">Lots</th><th>Closes</th>
                <th className="num">Premium</th><th></th><th></th>
              </tr>
            </thead>
            <tbody>
              {auctionRowsForDisplay.slice(0, auctionLimit).map((a) => a.header ? (
                <tr key={`hdr-${a.header}`}>
                  <td colSpan={7} style={{ paddingTop: 12, background: 'var(--bg)' }}>
                    <button className="bare"
                            onClick={() => toggleSection(a.sectionKey)}
                            aria-expanded={!a.collapsed}
                            title={a.collapsed ? 'Show these auctions' : 'Hide these auctions'}
                            style={{ fontWeight: 700, fontSize: 14, padding: 0 }}>
                      {a.collapsed ? '▸' : '▾'} {a.header}
                    </button>
                  </td>
                </tr>
              ) : (
                <tr key={a.id}
                    className={isHotAuction(a) ? 'row-gold' : undefined}
                    style={{
                      opacity: isUnderFloor(a) ? 0.55 : undefined,
                      background: !isHotAuction(a) && selectedAuctions.includes(a.id)
                        ? 'var(--highlight)' : undefined,
                    }}>
                  <td style={{ paddingRight: 12 }}>
                    <a href={a.source_url} target="_blank" rel="noreferrer">{a.name}</a>
                    {goldBadge(a) && (
                      <div style={{ fontSize: 12, fontWeight: 600 }}>{goldBadge(a)}</div>
                    )}
                    <div style={{ fontSize: 11, color: 'var(--muted)' }}>{auctionState(a).text}</div>
                  </td>
                  <td style={{ paddingRight: 12 }}>
                    {isClosed(a) && <strong>CLOSED<br /></strong>}
                    {a.city}, {a.state}{fulfillment(a) ? ` (${fulfillment(a)})` : ''}
                    {shipBadge(a) && (
                      <div style={{ fontSize: 11, color: 'var(--muted)' }}
                           title={shipBadge(a).tip}>{shipBadge(a).text}</div>
                    )}
                    {houseRatioLabel(a.estimate_ratio, a.estimate_ratio_n) && (
                      <div style={{ fontSize: 11, color: 'var(--muted)', cursor: 'help' }}
                           title={houseRatioTitle(a.estimate_ratio, a.estimate_ratio_n)}>
                        {houseRatioLabel(a.estimate_ratio, a.estimate_ratio_n)}
                      </div>
                    )}
                  </td>
                  <td className="num">
                    {a.lot_count ?? '—'}
                    {a.lots_imported > 0 && (
                      <div style={{ fontSize: 11, color: 'var(--muted)' }}>{a.lots_imported} imported</div>
                    )}
                    {hasCategoryCount(a) && (
                      <div style={{ fontSize: 11, color: 'var(--muted)' }}>{a.category_lot_count} match</div>
                    )}
                  </td>
                  <td style={{ whiteSpace: 'nowrap' }}>
                    {a.closing_date ? parseUtc(a.closing_date).toLocaleDateString() : '—'}
                  </td>
                  <td className="num">
                    {a.buyer_premium_mult ? `${Math.round((a.buyer_premium_mult - 1) * 100)}%` : '—'}
                  </td>
                  <td style={{ whiteSpace: 'nowrap' }}>
                    <button onClick={() => handleImport(a.id)} disabled={!!busy}>{importLabel(a)}</button>{' '}
                    <button className="bare"
                            onClick={() => confirmForget(a)}
                            title="Forget this auction — it won't come back in future scans"
                            style={{ color: 'var(--muted)', padding: '0 2px' }}>
                      ✕
                    </button>
                  </td>
                  <td style={{ whiteSpace: 'nowrap' }}>
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
        {!isMobile && auctionRowsForDisplay.length > auctionLimit && (
          <button style={{ marginTop: 8 }} onClick={() => setAuctionLimit((n) => n + 50)}>
            Show more auctions ({auctionRowsForDisplay.length - auctionLimit} more)
          </button>
        )}
      </section>
      )}

      {view === 'items' && (<>
      <section style={{ marginBottom: 6, display: 'flex',
                        flexDirection: 'column', gap: 6 }}>
        {/* Row 1: scope (auctions + category) on the left, bulk actions on
            the right — one wrapping row so the controls stop eating the
            viewport before any results show. */}
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'flex-start' }}>
          <details className="picker" style={{ position: 'relative', maxWidth: isMobile ? '100%' : 440 }}>
            <summary title="Tick one or more imported auctions to see just their items">
              {selectedAuctions.length
                ? `${selectedAuctions.length} auction${selectedAuctions.length === 1 ? '' : 's'} selected ▾`
                : `All auctions (${Object.keys(importedRows).length} imported) ▾`}
            </summary>
            <div className="panel">
              <button onClick={() => setSelectedAuctions([])}
                      disabled={!selectedAuctions.length}
                      style={{ width: '100%', padding: 6, fontSize: 13, marginBottom: 4 }}>
                Show all auctions
              </button>
              {Object.values(importedRows)
                .sort((a, b) => ((b.gold_count ?? 0) - (a.gold_count ?? 0)) || a.name.localeCompare(b.name))
                .map((a) => (
                  <label key={a.id}
                         style={{ display: 'flex', gap: 6, alignItems: 'flex-start',
                                  padding: '6px 4px', fontSize: 13, cursor: 'pointer' }}>
                    <input
                      type="checkbox"
                      checked={selectedAuctions.includes(a.id)}
                      onChange={() => toggleAuctionSelected(a.id)}
                      style={{ marginTop: 2 }}
                    />
                    <span style={{ lineHeight: 1.3 }}>
                      {a.name}
                      <div style={{ color: 'var(--muted)', fontSize: 12 }}>
                        {[a.city, a.state].filter(Boolean).join(', ') || '—'}
                        {a.lot_count != null ? ` · ${a.lots_imported} of ${a.lot_count} imported` : ''}
                        {a.gold_count ? ` · ${a.gold_count} gold` : ''}
                      </div>
                    </span>
                  </label>
                ))}
            </div>
          </details>
          {/* A selected auction with fewer lots in the DB than on HiBid gets
              a one-click "finish the import" — the usual arrival here is the
              View button or a lot's auction tag, where the gap is invisible. */}
          {selectedAuctions
            .map((id) => importedRows[id])
            .filter((a) => a && a.lot_count != null && a.lots_imported < a.lot_count
                           && !(a.closing_date && parseUtc(a.closing_date) < new Date()))
            .map((a) => (
              <button key={`partial-${a.id}`}
                      onClick={() => handleImport(a.id, -1)}
                      disabled={!!busy}
                      title={`"${a.name}" has ${a.lot_count} lots on HiBid but only ${a.lots_imported} in the database — import the rest (free, no AI calls)`}
                      style={{ padding: 8, fontSize: 14 }}>
                Import {a.lot_count - a.lots_imported} missing
                {selectedAuctions.length > 1 ? ` · ${a.name.length > 22 ? `${a.name.slice(0, 22)}…` : a.name}` : ''}
              </button>
            ))}
          <select
            value={categoryFilter}
            onChange={(ev) => setCategoryFilter(ev.target.value)}
            title="Show only items in one HiBid category — across every auction, or just the selected ones"
            style={{ padding: 8, fontSize: 14, maxWidth: isMobile ? '100%' : 320 }}
          >
            <option value="">All categories</option>
            {lotCategories.map((c) => (
              <option key={c.category} value={c.category}>{c.category} ({c.lots})</option>
            ))}
          </select>
          {categoryFilter && (() => {
            const cat = lotCategories.find((c) => c.category === categoryFilter)
            return (
              <button
                onClick={handleEnrichCategory}
                disabled={!cat?.enrichable}
                title="Work out a value for every unpriced item in this category, across ALL imported open auctions (asks first, shows cost)"
                style={{ padding: 8, fontSize: 14 }}
              >
                {cat?.enrichable ? `Price ${cat.enrichable} in category` : 'Category fully priced'}
              </button>
            )
          })()}
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8,
                        marginLeft: isMobile ? 0 : 'auto' }}>
            <button style={isMobile ? { flex: '1 1 45%', padding: 8 } : undefined}
                    onClick={handleRefreshBids}
                    title="Re-pull current bids from HiBid for every imported open auction and recompute ROI. Free — progress shows in the top bar.">
              Refresh bids
            </button>
            <button style={isMobile ? { flex: '1 1 45%', padding: 8 } : undefined}
                    onClick={handleInspectNoValue}
                    title="For every item still showing no value: AI reads the photo, identifies what is in it and prices it (asks first, shows cost)">
              Price the unpriced
            </button>
            <button className="danger"
                    style={isMobile ? { flex: '1 1 45%', padding: 8 } : undefined}
                    onClick={handleFlushClosed}
                    title="Permanently delete all items whose auction has closed (asks first)">
              Flush closed items
            </button>
          </div>
        </div>

        {/* Row 2: filters — inline-flex per label so a checkbox never wraps
            away from its own text, consistent gaps instead of ad-hoc margins */}
        <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center',
                      columnGap: 14, rowGap: 4, fontSize: 13 }}>
          <label style={{ display: 'inline-flex', alignItems: 'center', gap: 4, whiteSpace: 'nowrap' }}>
            <input
              type="checkbox"
              checked={filters.boloOnly}
              onChange={(ev) => setFilters((f) => ({ ...f, boloOnly: ev.target.checked }))}
            /> BOLO only
          </label>
          <label style={{ display: 'inline-flex', alignItems: 'center', gap: 4, whiteSpace: 'nowrap' }}>
            <input
              type="checkbox"
              checked={filters.roiStatus === 'GOLD MINE'}
              onChange={(ev) => setFilters((f) => ({ ...f, roiStatus: ev.target.checked ? 'GOLD MINE' : '' }))}
            /> Gold mines only
          </label>
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4, whiteSpace: 'nowrap',
                         color: 'var(--muted)' }}
                title="An item is a GOLD MINE when its current bid still clears this return after all fees. Saving re-grades every item for free.">
            at
            <input
              type="number"
              value={targetRoi}
              onChange={(ev) => setTargetRoi(ev.target.value)}
              style={{ width: 58 }}
            />% ROI
            <button style={{ fontSize: 12, padding: '3px 9px' }} onClick={handleSaveRoi}>Apply</button>
          </span>
          <label style={{ display: 'inline-flex', alignItems: 'center', gap: 4, whiteSpace: 'nowrap' }}
                 title="Hide items priced under the cutoff when the value is trustworthy — 3+ comps agree, or the AI identified the item with strong confidence">
            <input
              type="checkbox"
              checked={hideLowValue}
              onChange={(ev) => setHideLowValue(ev.target.checked)}
            /> Hide low-value (&lt; $
            <input
              type="number"
              value={lowValueCutoff}
              onChange={(ev) => setLowValueCutoff(Number(ev.target.value) || 0)}
              style={{ width: 44 }}
            />)
          </label>
          <label style={{ display: 'inline-flex', alignItems: 'center', gap: 4, whiteSpace: 'nowrap' }}>
            <input
              type="checkbox"
              checked={hideHardShip}
              onChange={(ev) => setHideHardShip(ev.target.checked)}
            /> Hide HARD ship
          </label>
          <label style={{ display: 'inline-flex', alignItems: 'center', gap: 4, whiteSpace: 'nowrap' }}>
            <input
              type="checkbox"
              checked={hideClosed}
              onChange={(ev) => setHideClosed(ev.target.checked)}
            /> Hide closed
          </label>
          <label style={{ display: 'inline-flex', alignItems: 'center', gap: 4, whiteSpace: 'nowrap' }}
                 title="Lots you hid with the hide button — check to see and unhide them">
            <input
              type="checkbox"
              checked={showHiddenLots}
              onChange={(ev) => setShowHiddenLots(ev.target.checked)}
            /> Show hidden ({lots.filter((l) => l.hidden).length})
          </label>
          <label style={{ display: 'inline-flex', alignItems: 'center', gap: 4, whiteSpace: 'nowrap' }}
                 title="Only lots you marked won — your resale inventory">
            <input
              type="checkbox"
              checked={wonOnly}
              onChange={(ev) => setWonOnly(ev.target.checked)}
            /> Won only ({lots.filter((l) => l.won).length})
          </label>
          {(hideLowValue || hideHardShip || hideClosed || !showHiddenLots) && hiddenCount > 0 && (
            <span style={{ color: 'var(--muted)', whiteSpace: 'nowrap' }}>
              {hiddenCount} hidden
            </span>
          )}
        </div>

      </section>

      {/* One quiet line, not a banner: what's in view and how to widen it.
          "Back to auctions" is gone — the Auctions tab is right there. */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap',
                    fontSize: 13, color: 'var(--muted)', margin: '2px 0 8px' }}>
        {selectedAuctions.length ? (
          <>
            <span>
              {selectedAuctions.length === 1
                ? <>Catalogue of <strong style={{ color: 'var(--text)' }}>{auctionIndex[selectedAuctions[0]] ?? 'this auction'}</strong></>
                : <>Items from <strong style={{ color: 'var(--text)' }}>{selectedAuctions.length} selected auctions</strong></>}
              {categoryFilter && <> in <strong style={{ color: 'var(--text)' }}>{categoryFilter}</strong></>}
              {' '}— {(lotTotal || lots.length).toLocaleString()} lot{(lotTotal || lots.length) === 1 ? '' : 's'} imported
            </span>
            <button style={{ fontSize: 12, padding: '2px 8px' }}
                    onClick={() => setSelectedAuctions([])}>
              Show every auction
            </button>
          </>
        ) : (
          <span>
            <strong style={{ color: 'var(--text)' }}>{lotTotal.toLocaleString()} items</strong> imported across
            {' '}{new Set(lots.map((l) => l.auction_id)).size} auctions
            {lotTotal > lots.length && (
              <em>
                {' '}— showing the first {lots.length.toLocaleString()}; open one
                auction, or filter, to narrow it down
              </em>
            )}
          </span>
        )}
      </div>
      {lots.length === 0 ? (
        lotsLoadState === 'loading' ? (
          <p style={{ color: 'var(--muted)' }}><span className="spinner" /> Loading your items…</p>
        ) : lotsLoadState === 'error' || lotTotal > 0 ? (
          <div className="empty-state">
            <div className="big">📡</div>
            <div>
              Couldn't load your {lotTotal ? lotTotal.toLocaleString() : ''} items —
              the server is probably busy with a big job right now.
            </div>
            <button onClick={loadLots} style={{ marginTop: 10 }}>Try again</button>
          </div>
        ) : (
          <div className="empty-state">
            <div><strong>Nothing imported yet.</strong></div>
            <div style={{ marginTop: 4 }}>
              Go to <strong>Auctions</strong>, find an auction, and press{' '}
              <strong>Import</strong> — its lots land here.
            </div>
            <button className="primary" style={{ marginTop: 12 }}
                    onClick={() => setView('auctions')}>
              Find auctions
            </button>
          </div>
        )
      ) : (
        <LotTable lots={visibleLots} onLotUpdated={handleLotUpdated} onRefresh={loadLots}
                  onSelectAuction={(id) => setSelectedAuctions([id])}
                  onLotTouched={markTouched} />
      )}
      </>)}
      </div>
    </div>
  )
}
