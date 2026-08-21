// Use whatever host the page was loaded from — localhost on the laptop, the
// laptop's LAN IP when opened from a phone on the same Wi-Fi.
const API_BASE = `http://${window.location.hostname}:8000`

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

export function scanAuctions({ nationwide = false } = {}) {
  return request('/auctions/scan', {
    method: 'POST',
    body: JSON.stringify({ include_nationwide: nationwide }),
  })
}

export function importLots(auctionId) {
  return request(`/auctions/${auctionId}/import`, { method: 'POST' })
}

export function enrichAll(auctionId) {
  return request(`/auctions/${auctionId}/enrich-all`, { method: 'POST' })
}
