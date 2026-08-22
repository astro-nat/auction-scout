// Deployed builds bake in the backend's public URL via VITE_API_BASE.
// In dev there's no env var, so fall back to whatever host the page was
// loaded from — localhost on the laptop, the laptop's LAN IP from a phone.
const API_BASE =
  import.meta.env.VITE_API_BASE || `http://${window.location.hostname}:8000`

async function request(path, options = {}) {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  if (!res.ok) throw new Error(`${options.method || 'GET'} ${path}: ${res.status}`)
  return res.json()
}

export function fetchLots({ auctionId, status, roiStatus, boloOnly } = {}) {
  const params = new URLSearchParams()
  params.set('limit', '2000')
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

export function enrichAll(auctionId) {
  return request(`/auctions/${auctionId}/enrich-all`, { method: 'POST' })
}
