const API_BASE = 'http://localhost:8000'

export async function fetchLots({ category, status } = {}) {
  const params = new URLSearchParams()
  if (category) params.set('category', category)
  if (status) params.set('status', status)

  const res = await fetch(`${API_BASE}/lots?${params}`)
  if (!res.ok) throw new Error(`Failed to fetch lots: ${res.status}`)
  return res.json()
}

export async function enrichLot(lotId) {
  const res = await fetch(`${API_BASE}/lots/${lotId}/enrich`, { method: 'POST' })
  if (!res.ok) throw new Error(`Failed to enrich lot: ${res.status}`)
  return res.json()
}

export async function fetchLot(lotId) {
  const res = await fetch(`${API_BASE}/lots/${lotId}`)
  if (!res.ok) throw new Error(`Failed to fetch lot: ${res.status}`)
  return res.json()
}
