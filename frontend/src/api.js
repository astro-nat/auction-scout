// Deployed builds bake in the backend's public URL via VITE_API_BASE.
// In dev there's no env var, so fall back to whatever host the page was
// loaded from — localhost on the laptop, the laptop's LAN IP from a phone.
// Guarded so the module loads under vitest (node, no window): the tracker's
// pure core imports this file for postEvents, and the tests import the core.
export const API_BASE =
  import.meta.env.VITE_API_BASE
  || `http://${typeof window !== 'undefined' ? window.location.hostname : 'localhost'}:8000`

// A deploy restart or a brief overload is gone in a few seconds — the old
// behaviour surfaced it as a dead-end popup telling the person to click
// again themselves. Retrying quietly here does that for them: three tries,
// short backoff, and the popup only fires if the server is still
// unreachable after ~7 seconds of real trying.
const RETRY_DELAYS_MS = [1000, 2000, 4000]
const RETRYABLE_STATUSES = new Set([502, 503, 504])   // a live gateway saying "not yet"
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

async function request(path, options = {}) {
  const what = `${options.method || 'GET'} ${path}`
  for (let attempt = 0; ; attempt++) {
    const isLastAttempt = attempt === RETRY_DELAYS_MS.length
    let res
    try {
      res = await fetch(`${API_BASE}${path}`, {
        headers: { 'Content-Type': 'application/json' },
        ...options,
      })
    } catch {
      // fetch() rejects with a bare "Failed to fetch" on any network-level
      // problem — say what was being attempted and the likely cause instead.
      if (!isLastAttempt) { await sleep(RETRY_DELAYS_MS[attempt]); continue }
      throw new Error(`Can't reach the server right now (${what}). `
        + `It's probably restarting after a deploy or briefly overloaded — `
        + `wait a few seconds and try again.`)
    }
    if (!res.ok) {
      if (RETRYABLE_STATUSES.has(res.status) && !isLastAttempt) {
        await sleep(RETRY_DELAYS_MS[attempt]); continue
      }
      // FastAPI puts the human-readable reason in {"detail": ...}.
      let detail = ''
      try { detail = (await res.json()).detail || '' } catch { /* not JSON */ }
      throw new Error(`${what} failed (${res.status})${detail ? `: ${detail}` : ''}`)
    }
    return res.json()
  }
}

// Deduped alert — repeated identical errors within a few seconds (e.g. a
// couple of clicks during a server restart) show one popup, not a storm.
let _lastAlert = { msg: '', t: 0 }
export function alertOnce(msg) {
  const now = Date.now()
  if (msg === _lastAlert.msg && now - _lastAlert.t < 5000) return
  _lastAlert = { msg, t: now }
  window.alert(msg)
}

export function fetchLots({ auctionIds, category, status, roiStatus, boloOnly, pricedOnly, flaggedOnly, offset } = {}) {
  const params = new URLSearchParams()
  // 2000 per page everywhere: a 6000-lot payload crashed phone tabs and
  // strained the backend. Big auctions page in with `offset` via the
  // "Load more" button instead.
  params.set('limit', '2000')
  if (offset) params.set('offset', String(offset))
  // Repeated param = "any of these auctions" server-side.
  for (const id of auctionIds || []) params.append('auction_id', id)
  if (category) params.set('category', category)
  if (status) params.set('status', status)
  if (roiStatus) params.set('roi_status', roiStatus)
  if (boloOnly) params.set('bolo_only', 'true')
  if (pricedOnly) params.set('priced_only', 'true')
  if (flaggedOnly) params.set('flagged_only', 'true')
  return request(`/lots?${params}`)
}

export function fetchLot(lotId) {
  return request(`/lots/${lotId}`)
}

export function enrichLot(lotId) {
  return request(`/lots/${lotId}/enrich`, { method: 'POST' })
}

export function inspectLot(lotId) {
  return request(`/lots/${lotId}/inspect`, { method: 'POST' })
}

// One lot, sold comps on its own title, no AI - the row's default button.
export function compsLot(lotId) {
  return request(`/lots/${lotId}/comps`, { method: 'POST' })
}

// Deliberately release the AI lock on one lot and price it again. One
// lot at a time by design - there is no bulk form of this.
export function recheckLot(lotId) {
  return request(`/lots/${lotId}/recheck`, { method: 'POST' })
}

// Pull one item forward inside a running job's remaining work - the list
// the Queue view shows under "Next up".
export function moveJobItem(jobId, itemId, to) {
  return request(`/queue/jobs/${jobId}/items/${itemId}/move?to=${to}`,
                 { method: 'POST' })
}

export function enrichBatch(lotIds) {
  return request('/lots/enrich-batch', {
    method: 'POST',
    body: JSON.stringify({ lot_ids: lotIds }),
  })
}

export function patchEnrichment(lotId, changes) {
  return request(`/lots/${lotId}/enrichment`, {
    method: 'PATCH',
    body: JSON.stringify(changes),
  })
}

// "These comps came back wrong" — independent of a hand-corrected value,
// so it survives the next reprice instead of blocking one. flagged=false
// clears it.
export function flagComp(lotId, { flagged = true, note } = {}) {
  return request(`/lots/${lotId}/flag-comp`, {
    method: 'POST',
    body: JSON.stringify({ flagged, note }),
  })
}

export function fetchLotCount({ auctionIds, category, status, roiStatus, boloOnly, pricedOnly, flaggedOnly } = {}) {
  const params = new URLSearchParams()
  for (const id of auctionIds || []) params.append('auction_id', id)
  if (category) params.set('category', category)
  if (status) params.set('status', status)
  if (roiStatus) params.set('roi_status', roiStatus)
  if (boloOnly) params.set('bolo_only', 'true')
  if (pricedOnly) params.set('priced_only', 'true')
  if (flaggedOnly) params.set('flagged_only', 'true')
  return request(`/lots/count?${params}`)
}

// Categories of the lots actually in the database (with enrichable counts) —
// not to be confused with fetchCategories(), HiBid's scan-filter tree.
export function fetchLotCategories() {
  return request('/lots/categories')
}

export function enrichCategory(category, { skipHard = false, dryRun = false } = {}) {
  const params = new URLSearchParams({ category })
  if (skipHard) params.set('skip_hard', 'true')
  if (dryRun) params.set('dry_run', 'true')
  return request(`/lots/enrich-category?${params}`, { method: 'POST' })
}

// Comps-only first pass: every never-priced lot in an open auction, searched
// on its raw title. No AI spend; about one SoldComps request per lot.
export function repriceUnpriced({ auctionIds = [], category = '', dryRun = false } = {}) {
  const params = new URLSearchParams({ unpriced_only: 'true' })
  for (const id of auctionIds) params.append('auction_ids', String(id))
  if (category) params.set('category', category)
  if (dryRun) params.set('dry_run', 'true')
  return request(`/lots/reprice?${params}`, { method: 'POST' })
}

// Comps-only on an explicit selection: the lots the user ticked, whatever
// their state. Raw title when there is no AI one; no AI spend either way.
export function repriceSelected(lotIds, { dryRun = false } = {}) {
  return request(`/lots/reprice${dryRun ? '?dry_run=true' : ''}`, {
    method: 'POST',
    body: JSON.stringify({ lot_ids: lotIds }),
  })
}

// Usage events, a few seconds' worth per call. Fire-and-forget: the
// tracker swallows failures, so losing a batch never surfaces as an error.
export function postEvents(events) {
  return request('/events', { method: 'POST', body: JSON.stringify({ events }) })
}

export function fetchEventSummary(days = 7) {
  return request(`/events/summary?days=${days}`)
}

export function fetchStatus() {
  return request('/status')
}

export function cancelJob(jobId) {
  return request(`/jobs/${jobId}/cancel`, { method: 'POST' })
}

export function cancelEnrichment() {
  return request('/enrichment/cancel', { method: 'POST' })
}

// Everything started and not yet finished, with what is left of each.
export function fetchQueue() {
  return request('/queue')
}

// Reorder the queue: to = top | up | down | bottom.
export function moveQueuedJob(jobId, to) {
  return request(`/queue/jobs/${jobId}/move?to=${to}`, { method: 'POST' })
}

export function moveQueuedLot(lotDbId, to) {
  return request(`/queue/lots/${lotDbId}/move?to=${to}`, { method: 'POST' })
}

export function fetchAuctions() {
  return request('/auctions')
}

// Watched auction houses. Keyed by HiBid's company id — the number in
// hibid.com/company/149798/budget-barn — so a house that renames itself
// stays watched. This is AuctionScout's own list, not HiBid's stars:
// reading those would need your HiBid login.
export function addFavoriteHouse({ auctionId, company, name }) {
  return request('/auctions/favorites', {
    method: 'POST',
    body: JSON.stringify({ auction_id: auctionId, company, name }),
  })
}

export function removeFavoriteHouse(companyId) {
  return request(`/auctions/favorites/${companyId}`, { method: 'DELETE' })
}

export function setAuctionHidden(auctionId, hidden) {
  return request(`/auctions/${auctionId}/hide?hidden=${hidden}`, { method: 'POST' })
}

// Forgotten auctions are skipped at scan time, so this list is the only way
// back — keyed by HiBid event id, which outlives our own auction row.
export function fetchDismissed() {
  return request('/auctions/dismissed')
}

export function undismissAuction(hibidId) {
  return request(`/auctions/dismissed/${hibidId}`, { method: 'DELETE' })
}

export function fetchCategories() {
  return request('/auctions/categories')
}

export function scanAuctions(filters = {}) {
  return request('/auctions/scan', {
    method: 'POST',
    body: JSON.stringify(filters),
  })
}

// GovDeals: one card per government seller near the zip. Import pulls that
// seller's open assets in as lots (and re-importing refreshes their bids).
export function scanGovDeals(filters = {}) {
  return request('/govdeals/scan', {
    method: 'POST',
    body: JSON.stringify(filters),
  })
}

export function importGovDeals(auctionId) {
  return request(`/govdeals/${auctionId}/import`, { method: 'POST' })
}

// PublicSurplus: one card per search area (the listing rows don't name the
// selling agency, so the radius is the grouping). Import doubles as refresh.
export function scanPublicSurplus(filters = {}) {
  return request('/publicsurplus/scan', {
    method: 'POST',
    body: JSON.stringify(filters),
  })
}

export function importPublicSurplus(auctionId) {
  return request(`/publicsurplus/${auctionId}/import`, { method: 'POST' })
}

// Vinted: a fixed-price WATCH — scanning a query imports the newest
// matching listings in the same call, and re-scanning refreshes prices
// and closes out whatever sold.
// Everything one Vinted seller has listed, as an auction of its own.
export function importVintedSeller(sellerId) {
  return request(`/vinted/seller/${sellerId}/import`, { method: 'POST' })
}

export function scanVinted(query, maxPrice) {
  return request('/vinted/scan', {
    method: 'POST',
    body: JSON.stringify({ query, max_price: maxPrice || undefined }),
  })
}

export function importLots(auctionId, categoryId = -1, searchText = '') {
  const params = new URLSearchParams()
  if (categoryId && categoryId !== -1) params.set('category_id', categoryId)
  if (searchText) params.set('search_text', searchText)
  const q = params.toString()
  return request(`/auctions/${auctionId}/import${q ? `?${q}` : ''}`, { method: 'POST' })
}

// One background job that imports every listed auction, in the order sent.
// Four shapes through one call: everything, one HiBid category, only lots
// whose title matches the BOLO brand list, or only lots matching a keyword
// - and they compose. The BOLO match runs at import and is free - regex
// over the title the fetch already returned, no AI - so the filter costs
// nothing; the keyword filter is applied by HiBid's own lot search, the
// same query the scan used to count matches.
export function importAllAuctions(auctionIds, categoryId = -1, boloOnly = false,
                                  searchText = '') {
  return request('/auctions/import-all', {
    method: 'POST',
    body: JSON.stringify({
      auction_ids: auctionIds,
      category_id: categoryId,
      bolo_only: boloOnly,
      search_text: searchText,
    }),
  })
}

export function enrichAll(auctionId, skipHard = false) {
  const q = skipHard ? '?skip_hard=true' : ''
  return request(`/auctions/${auctionId}/enrich-all${q}`, { method: 'POST' })
}

export function flushClosed(dryRun = false) {
  const q = dryRun ? '?dry_run=true' : ''
  return request(`/lots/flush-closed${q}`, { method: 'POST' })
}

export function analyzeShipping(dryRun = false) {
  const q = dryRun ? '?dry_run=true' : ''
  return request(`/auctions/analyze-shipping${q}`, { method: 'POST' })
}

export function setWatch(lotId, watched) {
  return request(`/lots/${lotId}/watch?watched=${watched}`, { method: 'POST' })
}

export function setHidden(lotId, hidden) {
  return request(`/lots/${lotId}/hide?hidden=${hidden}`, { method: 'POST' })
}


export function refreshBids() {
  return request('/auctions/refresh-bids', { method: 'POST' })
}

export function fetchSettings() {
  return request('/settings')
}

export function saveTargetRoi(pct) {
  return request('/settings', { method: 'PATCH', body: JSON.stringify({ target_roi_pct: pct }) })
}


export function reinspectNoComps(dryRun = false) {
  const q = dryRun ? '?dry_run=true' : ''
  return request(`/lots/reinspect-no-comps${q}`, { method: 'POST' })
}

// parseUtc moved to lib/time.js (pure modules and tests need it without the
// fetch layer); re-exported here so existing imports keep working.
export { parseUtc } from './lib/time'
