import { useEffect, useMemo, useRef, useState } from 'react'
import { enrichLot, inspectLot, fetchLot, patchEnrichment, enrichBatch, setWatch, setHidden, alertOnce, parseUtc } from '../api'
import useMediaQuery from '../useMediaQuery'

// Cell chrome (padding, borders) lives in index.css under .data-table;
// this only carries per-cell overrides now.
const cell = {}

const VERDICTS = [
  'broken, damaged, or for parts',
  'untested or unknown condition',
  'mint condition or working perfectly',
  'normal wear and tear',
]
const SHIP_TIERS = ['EASY', 'NEUTRAL', 'HARD']
const MONEY_RANGES = ['<5', '<10', '<25', '<50', '<100']

function money(v) {
  if (v === null || v === undefined) return '—'
  return `$${Number(v).toFixed(2)}`
}

const num = (v) => (v === null || v === undefined ? null : Number(v))

// What has actually happened to this lot, in words rather than jargon.
function statusLabel(e) {
  if (e.status === 'success') {
    return e.ai_source === 'vision-itemized' ? '✓ inspected' : '✓ enriched'
  }
  if (e.status === 'failed') return '✗ failed'
  if (e.status === 'queued') return 'queued'
  return 'imported, not enriched'
}

// Column definitions. `get` drives sorting and filtering; `filter` picks the
// filter widget: 'text' (title search), 'range' (money <N presets), or
// 'values' (dropdown of the distinct values present in the loaded lots).
const COLUMNS = [
  // Natural sort on the house's catalog number: "101" < "102" < "1001",
  // and suffixed numbers ("214A") sort with their base.
  { key: 'lot_number', label: '#',
    get: (l) => { const m = /^\s*(\d+)/.exec(l.lot_number || ''); return m ? Number(m[1]) : null },
    filter: 'text', num: true },
  { key: 'title', label: 'Title', get: (l) => l.title?.toLowerCase(), filter: 'text' },
  { key: 'auction', label: 'Auction', get: (l) => l.auction_name, filter: 'values' },
  { key: 'category', label: 'Category', get: (l) => l.category, filter: 'values' },
  { key: 'closes', label: 'Closes', get: (l) => l.closes_at ? parseUtc(l.closes_at).getTime() : Number.MAX_SAFE_INTEGER, filter: null },
  { key: 'bid', label: 'Bid', get: (l) => num(l.current_bid), filter: 'range', num: true },
  { key: 'est_cost', label: 'Est Cost', get: (l) => num(l.est_cost), filter: 'range', num: true },
  { key: 'ship', label: 'Ship', get: (l) => l.logistics_ease, filter: 'values' },
  { key: 'est_resale', label: 'Est Resale', get: (l) => num(l.enrichment?.est_resale), filter: 'range', num: true },
  { key: 'max_bid', label: 'Max Bid', get: (l) => num(l.enrichment?.max_bid), filter: 'range', num: true },
  { key: 'roi', label: 'ROI %', get: (l) => num(l.enrichment?.est_roi), filter: 'range', num: true },
  { key: 'verdict', label: 'Verdict', get: (l) => l.enrichment?.verdict, filter: 'values' },
  { key: 'status', label: 'Status', get: (l) => l.enrichment?.status, filter: 'values' },
]

// Live countdown text from an absolute close time. Bold/red inside two
// hours; "closed" once past; em-dash when HiBid gave no time.
function closesIn(closesAt, now) {
  if (!closesAt) return { text: '—', urgent: false }
  const ms = parseUtc(closesAt).getTime() - now
  if (ms <= 0) return { text: 'closed', urgent: false }
  const m = Math.floor(ms / 60000)
  const d = Math.floor(m / 1440), h = Math.floor((m % 1440) / 60), mm = m % 60
  const text = d > 0 ? `${d}d ${h}h` : h > 0 ? `${h}h ${mm}m` : `${mm}m`
  return { text, urgent: ms < 2 * 3600 * 1000 }
}

// Orange flag: the value is trustworthy (3+ comps or a strong AI
// identification) but bidding has already passed the max-bid ceiling —
// a real item you'd have wanted, gone over budget. Distinct from gold
// (still under ceiling) and from plain PASS (never worth chasing).
function isOverbid(lot, e) {
  const trustworthy = (e.comp_count ?? 0) >= 3 || e.confidence === 'strong'
  return trustworthy && e.max_bid != null && e.roi_status !== 'GOLD MINE'
    && Number(lot.current_bid ?? 0) > Number(e.max_bid) && Number(e.max_bid) > 0
}

// Why a $15 lot showing $60 resale returns 12% and not 300%: the ROI
// denominator is the ALL-IN cost (hammer + premium + tax + shipping +
// packing), and the resale side is net of eBay's cut.
// What kind of evidence a resale figure rests on. A gold mine built from
// asking prices is a much weaker claim than one built from completed sales:
// the Pearland industrial audit found all 62 of its golds priced off active
// listings, which sit unsold for months at aspirational prices.
function evidence(e) {
  const src = e.price_source || ''
  if (!e.est_resale) return null
  if (src.startsWith('retail $')) return 'retail'
  if (/AI-estimated/i.test(src)) return 'estimate'
  if (/\bsold\b/i.test(src) && !/\bactive\b/i.test(src)) return 'sold'
  if (/\bactive\b/i.test(src)) return 'asking'
  return null
}

const EVIDENCE_NOTE = {
  asking: 'Asking prices only — no confirmed sales. Sellers list high and wait.',
  estimate: 'Includes AI-estimated prices with no real comps behind them.',
  sold: 'Backed by completed sales.',
  retail: 'From the retail price printed in the lot title.',
}

// Gold, but resting on evidence weaker than real sales — rendered paler.
const isPaleEvidence = (ev) => ev === 'asking' || ev === 'estimate'

function roiTooltip(lot, e) {
  if (e.est_roi == null) return undefined
  const lines = [`Return on the all-in cost, not the hammer price.`]
  if (e.all_in_cost != null) {
    const pickup = /pickup/i.test(lot.source || '')
    lines.push(`All-in ${money(e.all_in_cost)} = bid ${money(lot.est_cost)} (w/ premium + tax)`
      + (pickup ? ` + packing` : ` + freight in + packing`))
    lines.push(pickup
      ? `Local pickup, so nothing to pay to get it here.`
      : `Buyer pays the outbound postage, so that isn't your cost.`)
  }
  if (e.est_resale != null) {
    lines.push(`Resale ${money(e.est_resale)} minus ~15% marketplace fee`
      + (e.profit != null ? ` → profit ${money(e.profit)}` : ''))
  }
  return lines.join(String.fromCharCode(10))
}

function matchesFilter(value, query) {
  if (!query) return true
  if (value == null) return false
  if (query.startsWith('>')) return typeof value === 'number' && value > parseFloat(query.slice(1))
  if (query.startsWith('<')) return typeof value === 'number' && value < parseFloat(query.slice(1))
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
  { label: 'ROI % (high first)', key: 'roi', dir: -1 },
  { label: 'Lot # (low first)', key: 'lot_number', dir: 1 },
  { label: 'Sort: unsorted', key: null, dir: 1 },
  { label: 'Est Resale (high first)', key: 'est_resale', dir: -1 },
  { label: 'Max Bid (high first)', key: 'max_bid', dir: -1 },
  { label: 'Current Bid (low first)', key: 'bid', dir: 1 },
  { label: 'Est Cost (low first)', key: 'est_cost', dir: 1 },
]

export default function LotTable({ lots, onLotUpdated, onRefresh, onLotTouched }) {
  const isMobile = useMediaQuery('(max-width: 768px)')
  const [pollingIds, setPollingIds] = useState(new Set())
  // Countdown clock — a 30s tick keeps every "closes in" cell live.
  const [now, setNow] = useState(Date.now())
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 30000)
    return () => clearInterval(t)
  }, [])
  // Best return first is the default view — that's the question the app
  // exists to answer. Click any header (or the mobile Sort menu) to change it.
  const [sort, setSort] = useState({ key: 'roi', dir: -1 })
  const [colFilters, setColFilters] = useState({})
  // Rows the user just enriched/inspected hold their screen position (and
  // App exempts them from hide filters) so the result can be read before
  // sorting sweeps it away. Pins release when the user re-sorts/re-filters.
  const pinnedPos = useRef(new Map())

  function pinLot(lotId) {
    const idx = sorted.findIndex((l) => l.lot_id === lotId)
    if (idx >= 0) pinnedPos.current.set(lotId, idx)
    onLotTouched?.(lotId)
  }
  // Render cap: building thousands of DOM rows eats real browser memory.
  // All lots stay loaded for filtering/sorting; we just paint them in pages.
  const [renderLimit, setRenderLimit] = useState(150)
  // True while the enrich-batch request is in flight.
  const [queuing, setQueuing] = useState(false)

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

  const sortedBase = useMemo(() => {
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

  // Splice pinned rows back to where the user last saw them.
  const sorted = useMemo(() => {
    const pins = pinnedPos.current
    if (!pins.size) return sortedBase
    const pinned = []
    const rest = []
    for (const l of sortedBase) {
      if (pins.has(l.lot_id)) pinned.push(l)
      else rest.push(l)
    }
    if (!pinned.length) return sortedBase
    pinned.sort((a, b) => pins.get(a.lot_id) - pins.get(b.lot_id))
    for (const l of pinned) rest.splice(Math.min(pins.get(l.lot_id), rest.length), 0, l)
    return rest
  }, [sortedBase])

  function handleSort(key) {
    pinnedPos.current.clear()
    setSort((prev) => (prev.key === key ? { key, dir: -prev.dir } : { key, dir: 1 }))
  }

  function setFilter(key, value) {
    pinnedPos.current.clear()
    setColFilters((prev) => ({ ...prev, [key]: value }))
  }

  async function handleEnrich(lotId) {
    try {
      pinLot(lotId)
      await enrichLot(lotId)
      setPollingIds((prev) => new Set(prev).add(lotId))
      poll(lotId)
    } catch (e) { alertOnce(e.message) }
  }

  async function handleInspect(lotId) {
    try {
      pinLot(lotId)
      await inspectLot(lotId)
      setPollingIds((prev) => new Set(prev).add(lotId))
      poll(lotId)
    } catch (e) { alertOnce(e.message) }
  }

  async function handleCorrect(lotId, field, value) {
    try {
      const updated = await patchEnrichment(lotId, { [field]: value === '' ? null : value })
      onLotUpdated(updated)
    } catch (e) { alertOnce(e.message) }
  }

  async function handleWatch(lotId, watched) {
    try {
      const updated = await setWatch(lotId, watched)
      onLotUpdated(updated)
    } catch (e) { alertOnce(e.message) }
  }

  async function handleHide(lotId, hidden) {
    try {
      const updated = await setHidden(lotId, hidden)
      onLotUpdated(updated)
    } catch (e) { alertOnce(e.message) }
  }

  function poll(lotId) {
    // Self-scheduling (setTimeout after each response), NOT setInterval:
    // an interval keeps firing while the server is slow — which it is
    // during the very inspection being polled — stacking dozens of
    // in-flight requests whose responses then land as a render storm.
    const tick = async () => {
      let updated
      try {
        updated = await fetchLot(lotId)
      } catch {
        setTimeout(tick, 8000)   // server busy — back off, retry
        return
      }
      onLotUpdated(updated)
      if (updated.enrichment?.status === 'success' || updated.enrichment?.status === 'failed') {
        setPollingIds((prev) => {
          const next = new Set(prev)
          next.delete(lotId)
          return next
        })
        return
      }
      setTimeout(tick, 4000)
    }
    setTimeout(tick, 3000)
  }

  // Every lot matching the current filters — not just the render window.
  // The render cap exists to save DOM memory, and capping the BATCH to it
  // meant "Enrich" on a 1,199-lot result stopped at 150. The confirm dialog
  // quoting the exact count and dollar cost is what guards the spend.
  const enrichable = sorted
    .filter((l) => !['success', 'queued'].includes(l.enrichment?.status))

  async function handleEnrichMatching() {
    if (!enrichable.length || queuing) return
    const cost = (enrichable.length * 0.005).toFixed(2)
    const ok = window.confirm(
      `Enrich all ${enrichable.length} lots matching your filters?

` +
      `Each one runs an AI pass and an eBay comp lookup — roughly $${cost} ` +
      `of API usage, processed in the order shown, top first.

` +
      `Progress appears in the bar at the top of the page.`
    )
    if (!ok) return
    // Busy state until the server answers: queueing a big batch takes a
    // moment, and total silence after "OK" read as the button being broken.
    setQueuing(true)
    try {
      const r = await enrichBatch(enrichable.map((l) => l.lot_id))
      onRefresh?.()
      if (!r.queued) {
        alert('Nothing to queue — those lots are already enriched or in progress.')
      }
    } catch (e) {
      // This had NO error handling — a failed request showed nothing at all.
      alertOnce(e.message)
    } finally {
      setQueuing(false)
    }
  }

  if (!lots.length) return <p>No lots yet — scan auctions and import one above.</p>

  const enrichableCount = enrichable.length
  // What's actually on screen right now vs. what the filters matched.
  const shownCount = Math.min(renderLimit, sorted.length)
  const countLine = (
    <span style={{ fontSize: 13, color: 'var(--muted)' }}>
      Showing <strong style={{ color: 'var(--fg, inherit)' }}>{shownCount.toLocaleString()}</strong>
      {' '}of {sorted.length.toLocaleString()} item{sorted.length === 1 ? '' : 's'}
      {sorted.length !== lots.length && ` (${lots.length.toLocaleString()} loaded, rest filtered out)`}
    </span>
  )

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
          <button className="primary" onClick={handleEnrichMatching}
                  disabled={!enrichableCount || queuing}
                  title="Enrich every lot matching the current filters — the whole result, not just the rows on screen. Asks for confirmation with the exact cost first."
                  style={{ flex: '1 1 100%', padding: 10, fontSize: 15 }}>
            {queuing ? <><span className="spinner" />Queuing {enrichableCount} lots…</>
                     : `Enrich all ${enrichableCount}`}
          </button>
          {anyQueued && <span style={{ flexBasis: '100%' }}><span className="spinner" />{lots.filter((l) => l.enrichment?.status === 'queued').length} lots in the queue… auto-refreshing</span>}
          <div style={{ flexBasis: '100%' }}>{countLine}</div>
        </div>
        {sorted.slice(0, renderLimit).map((lot) => {
          const e = lot.enrichment || {}
          const gold = e.roi_status === 'GOLD MINE'
          const overbid = isOverbid(lot, e)
          return (
            <div key={lot.lot_id}
                 className={`card${gold ? ' row-gold' : overbid ? ' row-overbid' : ''}`}
                 style={{ marginBottom: 10 }}
                 title={overbid ? 'Bid has passed your max-bid ceiling' : undefined}>
              <div style={{ fontWeight: 600 }}>
                <button
                  className="bare"
                  onClick={() => handleWatch(lot.lot_id, !lot.watched)}
                  title={lot.watched ? 'Watching — phone alert when closing (tap to stop)'
                                     : 'Watch: phone alert when this closes within 2 hours'}
                  style={{ fontSize: 16, padding: '0 4px 0 0',
                           opacity: lot.watched ? 1 : 0.45 }}>
                  {lot.watched ? '★' : '☆'}
                </button>
                <button
                  className="bare"
                  onClick={() => handleHide(lot.lot_id, !lot.hidden)}
                  title={lot.hidden ? 'Hidden — tap to bring it back' : 'Hide this lot'}
                  style={{ fontSize: 14, padding: '0 4px 0 0',
                           opacity: lot.hidden ? 1 : 0.4 }}>
                  {lot.hidden ? '👁' : '🚫'}
                </button>
                {lot.lot_number && (
                  <span style={{ color: 'var(--muted)', fontSize: 13, marginRight: 4 }}>
                    #{lot.lot_number}
                  </span>
                )}
                <a href={lot.lot_link} target="_blank" rel="noreferrer"
                   style={lot.hidden ? { opacity: 0.5, textDecoration: 'line-through' } : undefined}>{lot.title}</a>
              </div>
              <div style={{ color: 'var(--muted)', fontSize: 12 }}>
                {(lot.item_closed ?? lot.auction_closed) ? '⏹ closed · ' : ''}{lot.auction_name}
              </div>
              {e.enriched_title && e.enriched_title !== lot.title && (
                <div style={{ color: 'var(--muted)', fontSize: 13 }}>→ {e.enriched_title}</div>
              )}
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px 14px', fontSize: 14, margin: '6px 0' }}>
                <span style={closesIn(lot.closes_at, now).urgent
                             ? { color: 'var(--danger)', fontWeight: 700 } : undefined}>
                  ⏱ {closesIn(lot.closes_at, now).text}
                </span>
                <span>Bid {money(lot.current_bid)} / {money(lot.next_bid)}</span>
                <span>Cost {money(lot.est_cost)}</span>
                <span>Resale {money(e.est_resale)}{e.comp_count > 0 ? ` (${e.comp_count})` : ''}</span>
                <span>Max bid {money(e.max_bid)}</span>
                {e.est_roi != null && (
                  <span style={{ fontWeight: 600,
                                 color: Number(e.est_roi) < 0 ? 'var(--danger)'
                                   : e.roi_status === 'GOLD MINE' ? 'var(--success)' : undefined }}>
                    ROI {Math.round(Number(e.est_roi) * 100)}%
                  </span>
                )}
              </div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginBottom: 6 }}>
                <span className="badge">{lot.logistics_ease}</span>
                {e.bolo_brand && (
                  <span className="badge bolo">BOLO: {e.bolo_brand} T{e.bolo_tier ?? '?'}</span>
                )}
                {e.auth_required && (
                  <span className="badge bolo"
                        title="Luxury-brand match — resale value depends on authentication; don't trust the comps until verified">
                    ⚠️ authenticate first
                  </span>
                )}
                {e.verdict && (
                  <span className="badge"
                        title={e.gold_check_note || undefined}>
                    {gold ? (e.gold_check === 'confirmed' ? '🟢✓' : '🟢')
                      : e.roi_status === 'PASS' ? '🔴' : ''} {e.verdict}
                  </span>
                )}
                <span className="badge">
                  {isWorking(lot) ? <><span className="spinner" />{e.progress || (e.status === 'queued' ? 'waiting in queue…' : 'working…')}</> : statusLabel(e)}
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
                        title="Enrich: match the lot against the BOLO brand list, have AI read the description (or the photo) to build a searchable title and judge condition, look up eBay comps, then compute max bid and ROI."
                        onClick={() => handleEnrich(lot.lot_id)}>Enrich</button>
                <button style={{ flex: 1, padding: 8 }} disabled={isWorking(lot)}
                        title="Inspect: for mixed lots (a box of CDs, a tray of tools) — AI reads the full-size photo, lists each item it can identify, prices them individually, and totals them. Slower and costs more than Enrich."
                        onClick={() => handleInspect(lot.lot_id)}>Inspect</button>
              </div>
            </div>
          )
        })}
        {sorted.length > renderLimit && (
          <button style={{ width: '100%', padding: 10, marginTop: 4 }}
                  onClick={() => setRenderLimit((n) => n + 150)}>
            Show more ({sorted.length - renderLimit} hidden)
          </button>
        )}
      </>
    )
  }

  return (
    <>
    <div style={{ marginBottom: '0.5rem' }}>
      <button className="primary" onClick={handleEnrichMatching}
              disabled={!enrichableCount || queuing}
              title="Enrich every lot matching the current filters — the whole result, not just the rows on screen. Asks for confirmation with the exact cost first.">
        {queuing ? <><span className="spinner" />Queuing {enrichableCount} lots…</>
                 : `Enrich all ${enrichableCount}`}
      </button>
      {anyQueued && <span style={{ marginLeft: '0.75rem' }}><span className="spinner" />{lots.filter((l) => l.enrichment?.status === 'queued').length} lots in the queue… auto-refreshing</span>}
      <span style={{ marginLeft: '0.75rem' }}>{countLine}</span>
    </div>
    {/* No overflow wrapper: an overflow-x container becomes the scrollport
        position:sticky binds to, and the thead's top offset then displaces
        it INSIDE the table by the status bar's height — a blank band with
        the header floating over the first rows whenever a job is running.
        A too-narrow window falls back to page-level horizontal scrolling. */}
    <table className="data-table lot-table">
      {/* Sticks below the status bar when one is showing (see StatusBar's
          --statusbar-h). Solid background or the rows scroll through it. */}
      <thead style={{
        position: 'sticky', top: 'var(--statusbar-h, 0px)', zIndex: 10,
        background: 'var(--card-bg)',
      }}>
        <tr>
          {COLUMNS.map((c) => (
            <th
              key={c.key}
              className={c.num ? 'num' : undefined}
              style={{ cursor: 'pointer', userSelect: 'none',
                       ...(c.key === 'title' ? { width: '28%', minWidth: 220 } : {}) }}
              onClick={() => handleSort(c.key)}
              title="Click to sort"
            >
              {c.label}
              {sort.key === c.key ? (sort.dir === 1 ? ' ▲' : ' ▼') : ''}
            </th>
          ))}
          <th></th>
        </tr>
        <tr className="filter-row">
          {COLUMNS.map((c) => (
            <th key={c.key} style={{ fontWeight: 'normal',
                                     textAlign: c.num ? 'right' : undefined }}>
              {!c.filter ? null : c.filter === 'text' ? (
                <input
                  value={colFilters[c.key] ?? ''}
                  onChange={(ev) => setFilter(c.key, ev.target.value)}
                  placeholder="search"
                  style={{ width: '90%', minWidth: 60 }}
                />
              ) : (
                <select
                  value={colFilters[c.key] ?? ''}
                  onChange={(ev) => setFilter(c.key, ev.target.value)}
                  style={{ maxWidth: 110 }}
                >
                  <option value="">all</option>
                  {(c.filter === 'range' ? MONEY_RANGES : distinctValues[c.key] ?? []).map((v) => (
                    <option key={v} value={v}>{v}</option>
                  ))}
                </select>
              )}
            </th>
          ))}
          <th>
            {Object.values(colFilters).some((v) => v?.trim()) && (
              <button style={{ fontSize: 12, padding: '3px 8px' }} onClick={() => setColFilters({})}>clear</button>
            )}
          </th>
        </tr>
      </thead>
      <tbody>
        {sorted.slice(0, renderLimit).map((lot) => {
          const e = lot.enrichment || {}
          const gold = e.roi_status === 'GOLD MINE'
          const ev = evidence(e)
          const paleGold = gold && isPaleEvidence(ev)
          const overbid = isOverbid(lot, e)
          const edited = new Set(e.user_overrides || [])
          return (
            <tr key={lot.lot_id}
                className={gold ? 'row-gold' : overbid ? 'row-overbid' : undefined}
                title={overbid ? 'Bid has passed your max-bid ceiling'
                  : paleGold ? EVIDENCE_NOTE[ev] : undefined}
                // Weak-evidence gold (asking prices, AI estimates) gets a
                // paler wash: worth a look, not the same claim as real sales.
                style={paleGold ? { opacity: 0.82 } : undefined}>
              <td className="num" style={{ ...cell, color: 'var(--muted)' }}>
                {lot.lot_number || '—'}
              </td>
              <td style={cell}>
                <button
                  className="bare"
                  onClick={() => handleWatch(lot.lot_id, !lot.watched)}
                  title={lot.watched
                    ? 'Watching — you get a phone alert when this closes within 2 hours (click to stop)'
                    : 'Watch: get a phone alert when this lot closes within 2 hours'}
                  style={{ fontSize: 15, padding: '0 4px 0 0',
                           opacity: lot.watched ? 1 : 0.45 }}>
                  {lot.watched ? '★' : '☆'}
                </button>
                <button
                  className="bare"
                  onClick={() => handleHide(lot.lot_id, !lot.hidden)}
                  title={lot.hidden
                    ? 'Hidden — click to bring it back'
                    : 'Hide this lot — it disappears from your items until you unhide it (Show hidden checkbox)'}
                  style={{ fontSize: 13, padding: '0 4px 0 0',
                           opacity: lot.hidden ? 1 : 0.4 }}>
                  {lot.hidden ? '👁' : '🚫'}
                </button>
                {e.bolo_brand && (
                  <span style={{ cursor: 'help', marginRight: 4 }}
                        title={`BOLO match: ${e.bolo_brand} (tier ${e.bolo_tier ?? '?'})`}>🎯</span>
                )}
                {e.auth_required && (
                  <span style={{ cursor: 'help', marginRight: 4 }}
                        title="Luxury/precious-metal match — resale depends on authentication; don't trust the comps until verified in hand">⚠️</span>
                )}
                <a href={lot.lot_link} target="_blank" rel="noreferrer"
                   style={lot.hidden ? { opacity: 0.5, textDecoration: 'line-through' } : undefined}>{lot.title}</a>
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
              <td style={{ ...cell, fontSize: 12, maxWidth: 140 }}>
                {(lot.item_closed ?? lot.auction_closed) && <div><strong>⏹ closed</strong></div>}
                {lot.auction_name}
              </td>
              <td style={{ ...cell, whiteSpace: 'nowrap' }}>{lot.category}</td>
              <td style={{ ...cell, whiteSpace: 'nowrap',
                           ...(closesIn(lot.closes_at, now).urgent
                               ? { color: 'var(--danger)', fontWeight: 700 } : {}) }}>
                {closesIn(lot.closes_at, now).text}
              </td>
              <td className="num" style={cell}>{money(lot.current_bid)} / {money(lot.next_bid)}</td>
              <td className="num" style={cell}>{money(lot.est_cost)}</td>
              <td style={cell}>
                <EditableCell
                  display={lot.logistics_ease ?? '—'}
                  rawValue={lot.logistics_ease}
                  options={SHIP_TIERS}
                  edited={edited.has('logistics_ease')}
                  onSave={(v) => handleCorrect(lot.lot_id, 'logistics_ease', v)}
                />
              </td>
              <td className="num" style={cell}>
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
                {isPaleEvidence(ev) && (
                  <span title={EVIDENCE_NOTE[ev]}
                        style={{ color: 'var(--muted)', fontSize: 12, cursor: 'help' }}> ~</span>
                )}
              </td>
              <td className="num" style={cell}>{money(e.max_bid)}</td>
              <td className="num" title={roiTooltip(lot, e)}
                  style={{ ...cell,
                           cursor: e.est_roi != null ? 'help' : undefined,
                           color: e.est_roi == null ? undefined
                             : Number(e.est_roi) < 0 ? 'var(--danger)'
                             : e.roi_status === 'GOLD MINE' ? 'var(--success)' : undefined,
                           fontWeight: e.roi_status === 'GOLD MINE' ? 600 : undefined }}>
                {e.est_roi == null ? '—' : `${Math.round(Number(e.est_roi) * 100)}%`}
                {e.all_in_cost != null && (
                  <div style={{ color: 'var(--muted)', fontSize: 11 }}>
                    all-in {money(e.all_in_cost)}
                  </div>
                )}
              </td>
              <td style={cell}>
                <span
                  title={e.gold_check === 'confirmed'
                    ? `AI double-checked this gold${e.gold_check_note ? `: ${e.gold_check_note}` : ''}`
                    : e.gold_check === 'demoted'
                      ? `AI demoted this gold — ${e.gold_check_note || 'value judged implausible'}`
                      : undefined}
                  style={e.gold_check ? { cursor: 'help' } : undefined}>
                  {gold ? (e.gold_check === 'confirmed' ? '🟢✓' : '🟢')
                    : e.roi_status === 'PASS' ? '🔴' : ''}
                </span>{' '}
                <EditableCell
                  display={e.verdict ?? '—'}
                  rawValue={e.verdict}
                  options={VERDICTS}
                  edited={edited.has('verdict')}
                  onSave={(v) => handleCorrect(lot.lot_id, 'verdict', v)}
                />
              </td>
              <td style={cell}>
                {isWorking(lot) ? <><span className="spinner" />{e.progress || (e.status === 'queued' ? 'waiting in queue…' : 'working…')}</> : statusLabel(e)}
                {e.status === 'failed' && e.error_message && (
                  <div style={{ color: 'var(--error)', fontSize: 12 }}>{e.error_message.slice(0, 80)}</div>
                )}
              </td>
              <td style={{ ...cell, whiteSpace: 'nowrap' }}>
                <button disabled={isWorking(lot)} title="Enrich: match the lot against the BOLO brand list, have AI read the description (or the photo) to build a searchable title and judge condition, look up eBay comps, then compute max bid and ROI."
                        onClick={() => handleEnrich(lot.lot_id)}>Enrich</button>{' '}
                <button disabled={isWorking(lot)} title="Inspect: for mixed lots (a box of CDs, a tray of tools) — AI reads the full-size photo, lists each item it can identify, prices them individually, and totals them. Slower and costs more than Enrich."
                        onClick={() => handleInspect(lot.lot_id)}>
                  Inspect
                </button>
              </td>
            </tr>
          )
        })}
      </tbody>
    </table>
    {sorted.length > renderLimit && (
      <button style={{ marginTop: 8, padding: '6px 14px' }}
              onClick={() => setRenderLimit((n) => n + 150)}>
        Show more ({sorted.length - renderLimit} hidden)
      </button>
    )}
    </>
  )
}
