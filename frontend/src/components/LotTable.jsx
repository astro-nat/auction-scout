import { useEffect, useMemo, useState } from 'react'
import { enrichLot, inspectLot, fetchLot, patchEnrichment, enrichBatch } from '../api'
import useMediaQuery from '../useMediaQuery'

const cell = { padding: '4px 10px', borderBottom: '1px solid var(--border)' }

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
  { key: 'auction', label: 'Auction', get: (l) => l.auction_name, filter: 'values' },
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
      style={{ cursor: 'pointer', borderBottom: '1px dashed var(--muted)' }}
    >
      {display}{edited ? ' ✎' : ''}
    </span>
  )
}

// Mobile sort choices — a dropdown replaces click-to-sort headers on phones.
const MOBILE_SORTS = [
  { label: 'Sort: default', key: null, dir: 1 },
  { label: 'Est Resale (high first)', key: 'est_resale', dir: -1 },
  { label: 'Max Bid (high first)', key: 'max_bid', dir: -1 },
  { label: 'Current Bid (low first)', key: 'bid', dir: 1 },
  { label: 'Est Cost (low first)', key: 'est_cost', dir: 1 },
]

export default function LotTable({ lots, onLotUpdated, onRefresh }) {
  const isMobile = useMediaQuery('(max-width: 768px)')
  const [pollingIds, setPollingIds] = useState(new Set())
  const [sort, setSort] = useState({ key: null, dir: 1 })
  const [colFilters, setColFilters] = useState({})

  // Whenever ANY lot is queued — no matter which client or button started the
  // batch — refresh the table every 5s until the queue drains, so background
  // work is always visibly progressing.
  const anyQueued = lots.some((l) => l.enrichment?.status === 'queued')
  useEffect(() => {
    if (!anyQueued || !onRefresh) return
    const interval = setInterval(onRefresh, 5000)
    return () => clearInterval(interval)
  }, [anyQueued, onRefresh])

  // A lot is "working" when the server has it queued or this client just
  // kicked it off and is polling for the result.
  const isWorking = (lot) =>
    lot.enrichment?.status === 'queued' || pollingIds.has(lot.lot_id)

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

  async function handleEnrichVisible() {
    const ids = sorted
      .filter((l) => !['success', 'queued'].includes(l.enrichment?.status))
      .map((l) => l.lot_id)
    if (!ids.length) return
    const r = await enrichBatch(ids)
    onRefresh?.()
    alert(`Queued ${r.queued} visible lots — top of the table first`)
  }

  if (!lots.length) return <p>No lots yet — scan auctions and import one above.</p>

  const enrichableCount = sorted.filter(
    (l) => !['success', 'queued'].includes(l.enrichment?.status)
  ).length

  if (isMobile) {
    return (
      <>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginBottom: 10 }}>
          <input
            value={colFilters.title ?? ''}
            onChange={(ev) => setFilter('title', ev.target.value)}
            placeholder="Search lots…"
            style={{ flex: '1 1 100%', padding: 8, fontSize: 16 }}
          />
          <select
            value={colFilters.category ?? ''}
            onChange={(ev) => setFilter('category', ev.target.value)}
            style={{ flex: 1, padding: 6, fontSize: 14, maxWidth: '48%' }}
          >
            <option value="">All categories</option>
            {(distinctValues.category ?? []).map((v) => <option key={v} value={v}>{v}</option>)}
          </select>
          <select
            value={MOBILE_SORTS.findIndex((s) => s.key === sort.key && s.dir === sort.dir)}
            onChange={(ev) => {
              const s = MOBILE_SORTS[Number(ev.target.value)] ?? MOBILE_SORTS[0]
              setSort({ key: s.key, dir: s.dir })
            }}
            style={{ flex: 1, padding: 6, fontSize: 14, maxWidth: '48%' }}
          >
            {MOBILE_SORTS.map((s, i) => <option key={s.label} value={i}>{s.label}</option>)}
          </select>
          <button onClick={handleEnrichVisible} disabled={!enrichableCount}
                  style={{ flex: '1 1 100%', padding: 10, fontSize: 15 }}>
            Enrich visible ({enrichableCount})
          </button>
          {anyQueued && <span style={{ flexBasis: '100%' }}><span className="spinner" />enriching in the background… auto-refreshing</span>}
        </div>
        {sorted.map((lot) => {
          const e = lot.enrichment || {}
          const gold = e.roi_status === 'GOLD MINE'
          return (
            <div key={lot.lot_id} style={{
              border: '1px solid var(--border)', borderRadius: 8, padding: 10, marginBottom: 10,
              background: gold ? 'var(--gold-bg)' : 'var(--card-bg)',
            }}>
              <div style={{ fontWeight: 600 }}>
                <a href={lot.lot_link} target="_blank" rel="noreferrer">{lot.title}</a>
              </div>
              <div style={{ color: 'var(--muted)', fontSize: 12 }}>{lot.auction_name}</div>
              {e.enriched_title && e.enriched_title !== lot.title && (
                <div style={{ color: 'var(--muted)', fontSize: 13 }}>→ {e.enriched_title}</div>
              )}
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px 14px', fontSize: 14, margin: '6px 0' }}>
                <span>Bid {money(lot.current_bid)} / {money(lot.next_bid)}</span>
                <span>Cost {money(lot.est_cost)}</span>
                <span>Resale {money(e.est_resale)}{e.comp_count > 0 ? ` (${e.comp_count})` : ''}</span>
                <span>Max bid {money(e.max_bid)}</span>
              </div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, fontSize: 12, marginBottom: 6 }}>
                <span style={{ background: 'var(--badge-bg)', borderRadius: 4, padding: '2px 6px' }}>{lot.logistics_ease}</span>
                {e.bolo_brand && (
                  <span style={{ background: 'var(--bolo-bg)', borderRadius: 4, padding: '2px 6px' }}>
                    BOLO: {e.bolo_brand} T{e.bolo_tier ?? '?'}
                  </span>
                )}
                {e.verdict && (
                  <span style={{ background: 'var(--badge-bg)', borderRadius: 4, padding: '2px 6px' }}>
                    {gold ? '🟢' : e.roi_status === 'PASS' ? '🔴' : ''} {e.verdict}
                  </span>
                )}
                <span style={{ background: 'var(--badge-bg)', borderRadius: 4, padding: '2px 6px' }}>
                  {isWorking(lot) ? <><span className="spinner" />working…</> : e.status}
                </span>
              </div>
              {e.notes && e.ai_source === 'vision-itemized' && (
                <details style={{ fontSize: 12, color: 'var(--muted)', marginBottom: 6 }}>
                  <summary>itemized breakdown</summary>
                  <pre style={{ whiteSpace: 'pre-wrap', margin: 0 }}>{e.notes}</pre>
                </details>
              )}
              <div style={{ display: 'flex', gap: 8 }}>
                <button style={{ flex: 1, padding: 8 }} disabled={isWorking(lot)}
                        onClick={() => handleEnrich(lot.lot_id)}>Enrich</button>
                <button style={{ flex: 1, padding: 8 }} disabled={isWorking(lot)}
                        onClick={() => handleInspect(lot.lot_id)}>Inspect</button>
              </div>
            </div>
          )
        })}
      </>
    )
  }

  return (
    <>
    <div style={{ marginBottom: '0.5rem' }}>
      <button onClick={handleEnrichVisible} disabled={!enrichableCount}>
        Enrich visible ({enrichableCount})
      </button>
      {anyQueued && <span style={{ marginLeft: '0.75rem' }}><span className="spinner" />enriching in the background… auto-refreshing</span>}
    </div>
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
            <tr key={lot.lot_id} style={gold ? { background: 'var(--gold-bg)' } : undefined}>
              <td style={cell}>
                <a href={lot.lot_link} target="_blank" rel="noreferrer">{lot.title}</a>
                <div style={{ color: 'var(--muted)', fontSize: 12 }}>
                  →{' '}
                  <EditableCell
                    display={e.enriched_title || '(no enriched title)'}
                    rawValue={e.enriched_title}
                    edited={edited.has('enriched_title')}
                    onSave={(v) => handleCorrect(lot.lot_id, 'enriched_title', v)}
                  />
                </div>
                {e.notes && e.ai_source === 'vision-itemized' && (
                  <details style={{ fontSize: 12, color: 'var(--muted)' }}>
                    <summary>itemized breakdown</summary>
                    <pre style={{ whiteSpace: 'pre-wrap', margin: 0 }}>{e.notes}</pre>
                  </details>
                )}
              </td>
              <td style={{ ...cell, fontSize: 12, maxWidth: 140 }}>{lot.auction_name}</td>
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
                  <span style={{ color: 'var(--muted)', fontSize: 12 }}> ({e.comp_count})</span>
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
                {isWorking(lot) ? <><span className="spinner" />working…</> : e.status}
                {e.status === 'failed' && e.error_message && (
                  <div style={{ color: 'var(--error)', fontSize: 12 }}>{e.error_message.slice(0, 80)}</div>
                )}
              </td>
              <td style={{ ...cell, whiteSpace: 'nowrap' }}>
                <button disabled={isWorking(lot)} onClick={() => handleEnrich(lot.lot_id)}>Enrich</button>{' '}
                <button disabled={isWorking(lot)} onClick={() => handleInspect(lot.lot_id)}
                        title="Identify and price each item in the photo individually">
                  Inspect
                </button>
              </td>
            </tr>
          )
        })}
      </tbody>
    </table>
    </>
  )
}
