// Deployed builds bake in the backend's public URL via VITE_API_BASE.
// In dev there's no env var, so fall back to whatever host the page was
// loaded from — localhost on the laptop, the laptop's LAN IP from a phone.
const API_BASE =
  import.meta.env.VITE_API_BASE || `http://${window.location.hostname}:8000`

async function request(path, options = {}) {
  const what = `${options.method || 'GET'} ${path}`
  let res
  try {
    res = await fetch(`${API_BASE}${path}`, {
      headers: { 'Content-Type': 'application/json' },
      ...options,
    })
  } catch {
    // fetch() rejects with a bare "Failed to fetch" on any network-level
    // problem — say what was being attempted and the likely cause instead.
    throw new Error(`Can't reach the server right now (${what}). `
      + `It's probably restarting after a deploy or briefly overloaded — `
      + `wait a few seconds and try again.`)
  }
  if (!res.ok) {
    // FastAPI puts the human-readable reason in {"detail": ...}.
    let detail = ''
    try { detail = (await res.json()).detail || '' } catch { /* not JSON */ }
    throw new Error(`${what} failed (${res.status})${detail ? `: ${detail}` : ''}`)
  }
  return res.json()
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

export function fetchLots({ auctionId, status, roiStatus, boloOnly, offset } = {}) {
  const params = new URLSearchParams()
  // 2000 per page everywhere: a 6000-lot payload crashed phone tabs and
  // strained the backend. Big auctions page in with `offset` via the
  // "Load more" button instead.
  params.set('limit', '2000')
  if (offset) params.set('offset', String(offset))
  if (auctionId) params.set('auction_id', auctionId)
  if (status) params.set('status', status)
  if (roiStatus) params.set('roi_status', roiStatus)
  if (boloOnly) params.set('bolo_only', 'true')
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

export function fetchLotCount({ auctionId, status, roiStatus, boloOnly } = {}) {
  const params = new URLSearchParams()
  if (auctionId) params.set('auction_id', auctionId)
  if (status) params.set('status', status)
  if (roiStatus) params.set('roi_status', roiStatus)
  if (boloOnly) params.set('bolo_only', 'true')
  return request(`/lots/count?${params}`)
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

export function fetchAuctions() {
  return request('/auctions')
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

export function importLots(auctionId, categoryId = -1) {
  const q = categoryId && categoryId !== -1 ? `?category_id=${categoryId}` : ''
  return request(`/auctions/${auctionId}/import${q}`, { method: 'POST' })
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
