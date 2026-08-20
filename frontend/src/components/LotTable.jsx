import { useMemo, useState } from 'react'
import { enrichLot, inspectLot, fetchLot, patchEnrichment } from '../api'

const cell = { padding: '4px 10px', borderBottom: '1px solid #ddd' }

const VERDICTS = [
  'broken, damaged, or for parts',
  'untested or unknown condition',
  'mint condition or working perfectly',
  'normal wear and tear',
]
const SHIP_TIERS = ['EASY', 'NEUTRAL', 'HARD']
const MONEY_RANGES = ['>5', '>10', '>25', '>50', '>100']

function money(v) {
  if (v === null || v === undefined) return '—'
  return `$${Number(v).toFixed(2)}`
}

const num = (v) => (v === null || v === undefined ? null : Number(v))

// Column definitions. `get` drives sorting and filtering; `filter` picks the
// filter widget: 'text' (title search), 'range' (money >N presets), or
// 'values' (dropdown of the distinct values present in the loaded lots).
const COLUMNS = [
  { key: 'title', label: 'Title', get: (l) => l.title?.toLowerCase(), filter: 'text' },
  { key: 'category', label: 'Category', get: (l) => l.category, filter: 'values' },
  { key: 'bid', label: 'Bid', get: (l) => num(l.current_bid), filter: 'range' },
  { key: 'est_cost', label: 'Est Cost', get: (l) => num(l.est_cost), filter: 'range' },
  { key: 'ship', label: 'Ship', get: (l) => l.logistics_ease, filter: 'values' },
  { key: 'bolo', label: 'BOLO', get: (l) => l.enrichment?.bolo_brand, filter: 'values' },
  { key: 'est_resale', label: 'Est Resale', get: (l) => num(l.enrichment?.est_resale), filter: 'range' },
  { key: 'max_bid', label: 'Max Bid', get: (l) => num(l.enrichment?.max_bid), filter: 'range' },
  { key: 'verdict', label: 'Verdict', get: (l) => l.enrichment?.verdict, filter: 'values' },
  { key: 'status', label: 'Status', get: (l) => l.enrichment?.status, filter: 'values' },
]

function matchesFilter(value, query) {
  if (!query) return true
  if (value == null) return false
  if (query.startsWith('>')) return typeof value === 'number' && value > parseFloat(query.slice(1))
  if (typeof value === 'number') return String(value).includes(query)
  return String(value).toLowerCase().includes(query.toLowerCase())
}

// Click-to-edit cell. Enter saves (PATCHes the correction to the backend,
// where it's remembered and protected from re-enrichment), Esc cancels.
function EditableCell({ display, rawValue, onSave, options, inputType = 'text', edited }) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState('')

  function start() {
    setDraft(rawValue ?? '')
    setEditing(true)
  }

  async function save(value) {
    setEditing(false)
    if (String(value) === String(rawValue ?? '')) return
    await onSave(value)
  }

  if (editing) {
    if (options) {
      return (
        <select
          autoFocus
          value={draft}
          onChange={(ev) => save(ev.target.value)}
          onBlur={() => setEditing(false)}
        >
          <option value="">—</option>
          {options.map((o) => <option key={o} value={o}>{o}</option>)}
        </select>
      )
    }
    return (
      <input
        autoFocus
        type={inputType}
        value={draft}
        onChange={(ev) => setDraft(ev.target.value)}
        onKeyDown={(ev) => {
          if (ev.key === 'Enter') save(draft)
          if (ev.key === 'Escape') setEditing(false)
        }}
        onBlur={() => setEditing(false)}
        style={{ width: inputType === 'number' ? 70 : 160, fontSize: 13 }}
      />
    )
  }
  return (
    <span
      onClick={start}
      title="Click to correct — your value is remembered and won't be overwritten"
      style={{ cursor: 'pointer', borderBottom: '1px dashed #bbb' }}
    >
      {display}{edited ? ' ✎' : ''}
    </span>
  )
}

export default function LotTable({ lots, onLotUpdated }) {
  const [pollingIds, setPollingIds] = useState(new Set())
  const [sort, setSort] = useState({ key: null, dir: 1 })
  const [colFilters, setColFilters] = useState({})

  const distinctValues = useMemo(() => {
    const out = {}
    for (const c of COLUMNS) {
      if (c.filter !== 'values') continue
      out[c.key] = [...new Set(lots.map((l) => c.get(l)).filter((v) => v != null && v !== ''))].sort()
    }
    return out
  }, [lots])

  const filtered = useMemo(() => {
    const active = COLUMNS.filter((c) => colFilters[c.key]?.trim())
    if (!active.length) return lots
    return lots.filter((l) => active.every((c) => matchesFilter(c.get(l), colFilters[c.key].trim())))
  }, [lots, colFilters])

  const sorted = useMemo(() => {
    if (!sort.key) return filtered
    const col = COLUMNS.find((c) => c.key === sort.key)
    return [...filtered].sort((a, b) => {
      const av = col.get(a)
      const bv = col.get(b)
      if (av == null && bv == null) return 0
      if (av == null) return 1
      if (bv == null) return -1
      if (av < bv) return -sort.dir
      if (av > bv) return sort.dir
      return 0
    })
  }, [filtered, sort])

  function handleSort(key) {
    setSort((prev) => (prev.key === key ? { key, dir: -prev.dir } : { key, dir: 1 }))
  }

  function setFilter(key, value) {
    setColFilters((prev) => ({ ...prev, [key]: value }))
  }

  async function handleEnrich(lotId) {
    await enrichLot(lotId)
    setPollingIds((prev) => new Set(prev).add(lotId))
    poll(lotId)
  }

  async function handleInspect(lotId) {
    await inspectLot(lotId)
    setPollingIds((prev) => new Set(prev).add(lotId))
    poll(lotId)
  }

  async function handleCorrect(lotId, field, value) {
    const updated = await patchEnrichment(lotId, { [field]: value === '' ? null : value })
    onLotUpdated(updated)
  }

  function poll(lotId) {
    const interval = setInterval(async () => {
      const updated = await fetchLot(lotId)
      if (updated.enrichment?.status === 'success' || updated.enrichment?.status === 'failed') {
        clearInterval(interval)
        setPollingIds((prev) => {
          const next = new Set(prev)
          next.delete(lotId)
          return next
        })
      }
      onLotUpdated(updated)
    }, 3000)
  }

  if (!lots.length) return <p>No lots yet — scan auctions and import one above.</p>

  return (
    <table style={{ borderCollapse: 'collapse', fontSize: 14 }}>
      <thead>
        <tr>
          {COLUMNS.map((c) => (
            <th
              key={c.key}
              style={{ ...cell, cursor: 'pointer', userSelect: 'none', whiteSpace: 'nowrap' }}
              onClick={() => handleSort(c.key)}
              title="Click to sort"
            >
              {c.label}
              {sort.key === c.key ? (sort.dir === 1 ? ' ▲' : ' ▼') : ''}
            </th>
          ))}
          <th style={cell}></th>
        </tr>
        <tr>
          {COLUMNS.map((c) => (
            <th key={c.key} style={{ ...cell, fontWeight: 'normal' }}>
              {c.filter === 'text' ? (
                <input
                  value={colFilters[c.key] ?? ''}
                  onChange={(ev) => setFilter(c.key, ev.target.value)}
                  placeholder="search"
                  style={{ width: '90%', minWidth: 60, fontSize: 12 }}
                />
              ) : (
                <select
                  value={colFilters[c.key] ?? ''}
                  onChange={(ev) => setFilter(c.key, ev.target.value)}
                  style={{ maxWidth: 110, fontSize: 12 }}
                >
                  <option value="">all</option>
                  {(c.filter === 'range' ? MONEY_RANGES : distinctValues[c.key] ?? []).map((v) => (
                    <option key={v} value={v}>{v}</option>
                  ))}
                </select>
              )}
            </th>
          ))}
          <th style={cell}>
            {Object.values(colFilters).some((v) => v?.trim()) && (
              <button style={{ fontSize: 12 }} onClick={() => setColFilters({})}>clear</button>
            )}
          </th>
        </tr>
      </thead>
      <tbody>
        {sorted.map((lot) => {
          const e = lot.enrichment || {}
          const gold = e.roi_status === 'GOLD MINE'
          const edited = new Set(e.user_overrides || [])
          return (
            <tr key={lot.lot_id} style={gold ? { background: '#e6ffe6' } : undefined}>
              <td style={cell}>
                <a href={lot.lot_link} target="_blank" rel="noreferrer">{lot.title}</a>
                <div style={{ color: '#666', fontSize: 12 }}>
                  →{' '}
                  <EditableCell
                    display={e.enriched_title || '(no enriched title)'}
                    rawValue={e.enriched_title}
                    edited={edited.has('enriched_title')}
                    onSave={(v) => handleCorrect(lot.lot_id, 'enriched_title', v)}
                  />
                </div>
                {e.notes && e.ai_source === 'vision-itemized' && (
                  <details style={{ fontSize: 12, color: '#444' }}>
                    <summary>itemized breakdown</summary>
                    <pre style={{ whiteSpace: 'pre-wrap', margin: 0 }}>{e.notes}</pre>
                  </details>
                )}
              </td>
              <td style={cell}>{lot.category}</td>
              <td style={cell}>{money(lot.current_bid)} / {money(lot.next_bid)}</td>
              <td style={cell}>{money(lot.est_cost)}</td>
              <td style={cell}>
                <EditableCell
                  display={lot.logistics_ease ?? '—'}
                  rawValue={lot.logistics_ease}
                  options={SHIP_TIERS}
                  edited={edited.has('logistics_ease')}
                  onSave={(v) => handleCorrect(lot.lot_id, 'logistics_ease', v)}
                />
              </td>
              <td style={cell}>
                <EditableCell
                  display={e.bolo_brand ? `${e.bolo_brand} (T${e.bolo_tier ?? '?'})` : '—'}
                  rawValue={e.bolo_brand}
                  edited={edited.has('bolo_brand')}
                  onSave={(v) => handleCorrect(lot.lot_id, 'bolo_brand', v)}
                />
              </td>
              <td style={cell}>
                <EditableCell
                  display={money(e.est_resale)}
                  rawValue={e.est_resale}
                  inputType="number"
                  edited={edited.has('est_resale')}
                  onSave={(v) => handleCorrect(lot.lot_id, 'est_resale', v)}
                />
                {e.comp_count > 0 && (
                  <span style={{ color: '#666', fontSize: 12 }}> ({e.comp_count})</span>
                )}
              </td>
              <td style={cell}>{money(e.max_bid)}</td>
              <td style={cell}>
                {gold ? '🟢' : e.roi_status === 'PASS' ? '🔴' : ''}{' '}
                <EditableCell
                  display={e.verdict ?? '—'}
                  rawValue={e.verdict}
                  options={VERDICTS}
                  edited={edited.has('verdict')}
                  onSave={(v) => handleCorrect(lot.lot_id, 'verdict', v)}
                />
              </td>
              <td style={cell}>
                {pollingIds.has(lot.lot_id) ? 'enriching…' : e.status}
                {e.status === 'failed' && e.error_message && (
                  <div style={{ color: '#a00', fontSize: 12 }}>{e.error_message.slice(0, 80)}</div>
                )}
              </td>
              <td style={{ ...cell, whiteSpace: 'nowrap' }}>
                <button onClick={() => handleEnrich(lot.lot_id)}>Enrich</button>{' '}
                <button onClick={() => handleInspect(lot.lot_id)} title="Identify and price each item in the photo individually">
                  Inspect
                </button>
              </td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}
