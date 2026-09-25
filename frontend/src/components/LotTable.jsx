import { useEffect, useMemo, useRef, useState } from 'react'
import { enrichLot, inspectLot, fetchLot, patchEnrichment, enrichBatch, repriceSelected, setWatch, setHidden, alertOnce, parseUtc } from '../api'
import { compRows, ebaySoldUrl } from '../lib/comps'
import { houseRatioLabel, houseRatioTitle } from '../lib/calibration'
import { CLOSING_RANGES, ROI_RANGES, hoursUntil, matchesFilter, presetsFor, roiPercent } from '../lib/filters'
import { allSelected, chunked, inView, selectAll, toggle } from '../lib/selection'
import { track } from '../lib/track'
import useMediaQuery from '../useMediaQuery'

// The homework behind a resale number: the comp records the pricer actually
// used (raw observed prices — never scaled to match the conclusion), plus a
// one-tap eBay sold-listings search so distrust costs thirty seconds, not a
// Google detour. Rows enriched before comps were stored still get the link.
function CompsPeek({ lot, e }) {
  const search = ebaySoldUrl(e.enriched_title || lot.title)
  const rows = compRows(e.comps)
  if (!search && !rows.length) return null
  return (
    <details style={{ marginTop: 2 }}>
      <summary style={{ color: 'var(--muted)', fontSize: 11, cursor: 'pointer' }}>
        evidence{rows.length ? ` (${rows.length})` : ''}
      </summary>
      <div style={{ fontSize: 11, textAlign: 'left', maxWidth: 360, padding: '4px 0' }}>
        {e.roi_reason && (
          <div style={{ marginBottom: 3 }}>
            {e.roi_status === 'PASS' ? 'not gold: ' : ''}{e.roi_reason}
          </div>
        )}
        {e.price_source && (
          <div style={{ color: 'var(--muted)', marginBottom: 3 }}>{e.price_source}</div>
        )}
        {rows.map((r, i) => (
          <div key={i} style={{ marginBottom: 2 }}>
            {r.label}
            {' — '}
            {r.url
              ? <a href={r.url} target="_blank" rel="noreferrer">{r.title}</a>
              : r.title}
          </div>
        ))}
        {!rows.length && (
          <div style={{ color: 'var(--muted)', marginBottom: 2 }}>
            no comp records stored (priced before they were kept)
          </div>
        )}
        {search && (
          <a href={search} target="_blank" rel="noreferrer">
            check eBay sold listings yourself
          </a>
        )}
      </div>
    </details>
  )
}

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
function money(v) {
  if (v === null || v === undefined) return '—'
  return `$${Number(v).toFixed(2)}`
}

// The auction house's own estimate range, compact ("$850–1,500").
function houseEstimate(lot) {
  if (lot.estimate_low == null) return null
  const fmt = (v) => Number(v).toLocaleString(undefined, { maximumFractionDigits: 0 })
  const low = fmt(lot.estimate_low)
  const high = fmt(lot.estimate_high ?? lot.estimate_low)
  return low === high ? `$${low}` : `$${low}–${high}`
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
  // Sorts by the instant (unknown last); FILTERS by hours until close, so
  // the presets read "< 24 hrs" and a closed lot matches none of them.
  { key: 'closes', label: 'Closes',
    get: (l) => l.closes_at ? parseUtc(l.closes_at).getTime() : Number.MAX_SAFE_INTEGER,
    filterGet: (l) => hoursUntil(l.closes_at, Date.now(), parseUtc),
    filter: 'range', ranges: CLOSING_RANGES },
  { key: 'bid', label: 'Bid', get: (l) => num(l.current_bid), filter: 'range', num: true },
  { key: 'est_cost', label: 'Est Cost', get: (l) => num(l.est_cost), filter: 'range', num: true },
  { key: 'ship', label: 'Ship', get: (l) => l.logistics_ease, filter: 'values' },
  { key: 'est_resale', label: 'Est Resale', get: (l) => num(l.enrichment?.est_resale), filter: 'range', num: true },
  { key: 'max_bid', label: 'Max Bid', get: (l) => num(l.enrichment?.max_bid), filter: 'range', num: true },
  // In percent, matching the cell, with "at least" presets: ROI is the one
  // column where "under N" is never the question.
  { key: 'roi', label: 'ROI %', get: (l) => roiPercent(l.enrichment?.est_roi),
    filter: 'range', ranges: ROI_RANGES, num: true },
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
  if (src.startsWith('audit-corrected')) return 'audit'
  if (/itemized/i.test(src)) return 'itemized'
  if (/AI-estimated/i.test(src)) return 'estimate'
  if (/sold/i.test(src) && !/active/i.test(src)) {
    // One or two agreeing sales is an anecdote, not a market.
    return (e.comp_count || 0) >= 3 ? 'sold' : 'thin'
  }
  if (/active/i.test(src)) return 'asking'
  return null
}

// One word for how far to trust the number. The full trail - every
// figure produced for this lot, including the ones that were rejected -
// stays in Postgres and is readable at /prices/history/{lot_id}. None of
// that belongs on a row being scanned at a glance.
const EVIDENCE_LABEL = {
  sold: 'sold comps',
  retail: 'retail price',
  audit: 'audited',
  thin: 'thin comps',
  itemized: 'itemised',
  asking: 'asking only',
  estimate: 'AI guess',
}

const EVIDENCE_NOTE = {
  asking: 'Asking prices only — no confirmed sales. Sellers list high and wait.',
  estimate: 'Includes AI-estimated prices with no real comps behind them.',
  thin: 'Real sales, but only one or two agreeing. One sale is an anecdote.',
  audit: 'The comp value was judged implausible and replaced by a second opinion.',
  itemized: 'Summed from the items the vision pass could identify in the photo.',
  sold: 'Backed by completed sales.',
  retail: 'From the retail price printed in the lot title.',
}

// Gold, but resting on evidence weaker than real sales — rendered paler.
const isPaleEvidence = (ev) =>
  ev === 'asking' || ev === 'estimate' || ev === 'thin'

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
  { label: 'Closes (soonest first)', key: 'closes', dir: 1 },
  { label: 'Sort: unsorted', key: null, dir: 1 },
  { label: 'Est Resale (high first)', key: 'est_resale', dir: -1 },
  { label: 'Max Bid (high first)', key: 'max_bid', dir: -1 },
  { label: 'Current Bid (low first)', key: 'bid', dir: 1 },
  { label: 'Est Cost (low first)', key: 'est_cost', dir: 1 },
  { label: 'Title (A→Z)', key: 'title', dir: 1 },
]

export default function LotTable({ lots, onLotUpdated, onRefresh, onLotTouched, onSelectAuction }) {
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
  // Phone layout: the filters live behind a toggle. Twelve selects at the
  // top of a 375px screen pushed the first card below the fold; the label
  // carries the active count so a hidden filter is never a mystery.
  const [showFilters, setShowFilters] = useState(false)
  const activeFilterCount = Object.values(colFilters).filter((v) => v?.trim()).length
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
  // Multi-select: lot_ids the user has ticked. Kept as a Set of ids rather
  // than row indexes so ticks survive re-sorting, re-filtering and the
  // reload when a batch finishes. Actions act on the ticked lots
  // still in the current result (see lib/selection.inView).
  const [selected, setSelected] = useState(() => new Set())

  // Lots still queued, for the note above the table. The table itself is
  // not reloaded while they run - it updates once, when the batch finishes.
  const anyQueued = lots.some((l) => l.enrichment?.status === 'queued')

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
    // filterGet lets a column sort by one value and filter by another - the
    // Closes column sorts by the instant but filters by hours until close.
    return lots.filter((l) => active.every((c) =>
      matchesFilter((c.filterGet ?? c.get)(l), colFilters[c.key].trim())))
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
    track('sort', { key })
    setSort((prev) => (prev.key === key ? { key, dir: -prev.dir } : { key, dir: 1 }))
  }

  // Filters are logged once the typing stops: "Johnny" typed into the title
  // box used to arrive as thirteen rows, one per keystroke.
  const filterLogTimers = useRef({})
  function setFilter(key, value) {
    pinnedPos.current.clear()
    // Cleared filters are not worth a row; a value is - "ROI >100" forty
    // times a week says what the app is for.
    clearTimeout(filterLogTimers.current[key])
    if (value?.trim()) {
      filterLogTimers.current[key] = setTimeout(
        () => track('filter', { key, value: String(value).slice(0, 40) }), 1200)
    }
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
  //
  // Comps first, AI second. The default prices every unpriced lot in the
  // result from sold comps on its own title - no AI. AI then goes only to
  // the lots worth a closer look: comps-priced at or above `aiMin`, where a
  // condition check and a sharper identification can move real money.
  const unpricedShown = sorted
    .filter((l) => l.enrichment?.est_resale == null && l.enrichment?.status !== 'queued')
  const [aiMin, setAiMin] = useState(50)
  const aiTargets = sorted
    .filter((l) => !['success', 'queued'].includes(l.enrichment?.status)
                   && l.enrichment?.est_resale != null
                   && Number(l.enrichment.est_resale) >= Number(aiMin || 0))
    .sort((a, b) => Number(b.enrichment.est_resale) - Number(a.enrichment.est_resale))

  async function handleCompsMatching() {
    if (!unpricedShown.length || queuing) return
    const ok = window.confirm(
      `Look up sold comps for the ${unpricedShown.length} unpriced lots matching your filters?\n\n` +
      `No AI cost - at most one SoldComps request per title (identical titles share one). ` +
      `Progress appears in the bar at the top of the page.`)
    if (!ok) return
    setQueuing(true)
    try {
      const r = await repriceSelected(unpricedShown.map((l) => l.lot_id))
      if (r.already_running) alert('A re-price is already running; let it finish first.')
      onRefresh?.()
    } catch (e) { alertOnce(e.message) }
    finally { setQueuing(false) }
  }

  async function handleEnrichMatching() {
    if (!aiTargets.length || queuing) return
    const cost = (aiTargets.length * 0.005).toFixed(2)
    const ok = window.confirm(
      `AI-check the ${aiTargets.length} lots worth $${aiMin}+ that haven't had one?

` +
      `AI reads each one's photo and listing for condition and a closer identification, ` +
      `then prices it again - roughly $${cost} of API usage, most valuable first.

` +
      `Progress appears in the bar at the top of the page.`
    )
    if (!ok) return
    // Busy state until the server answers: queueing a big batch takes a
    // moment, and total silence after "OK" read as the button being broken.
    setQueuing(true)
    try {
      const r = await enrichBatch(aiTargets.map((l) => l.lot_id))
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

  // --- bulk actions on the selection ------------------------------------
  const selectedInView = inView(selected, sorted)

  // Per-lot endpoints (hide, watch) a few at a time: sequential is slow on
  // a hundred lots, all-at-once is a request storm. Every settled result
  // updates its row; a failure names itself once and the rest carry on.
  async function bulkEach(ids, fn) {
    for (const chunk of chunked(ids, 6)) {
      const results = await Promise.allSettled(chunk.map(fn))
      for (const r of results) {
        if (r.status === 'fulfilled') onLotUpdated(r.value)
        else alertOnce(r.reason?.message || String(r.reason))
      }
    }
  }

  async function handleBulkPrice() {
    const ids = selectedInView
      .filter((l) => !['success', 'queued'].includes(l.enrichment?.status))
      .map((l) => l.lot_id)
    if (!ids.length) { alert('Every selected lot is already priced or in progress.'); return }
    const cost = (ids.length * 0.005).toFixed(2)
    if (!window.confirm(`Price ${ids.length} selected lots?\n\nEach runs an AI pass and a comp `
                        + `lookup — roughly $${cost} of API usage, in the order shown.`)) return
    setQueuing(true)
    try { await enrichBatch(ids); onRefresh?.() } catch (e) { alertOnce(e.message) }
    finally { setQueuing(false) }
  }

  async function handleBulkComps() {
    const ids = selectedInView.map((l) => l.lot_id)
    if (!window.confirm(`Look up sold comps for ${ids.length} selected lots, using their titles `
                        + `as-is?\n\nNo AI cost. About ${ids.length} SoldComps requests against `
                        + `your plan. Lots that already have a value are searched again.`)) return
    setQueuing(true)
    try {
      const r = await repriceSelected(ids)
      if (r.already_running) alert('A re-price is already running; let it finish first.')
      onRefresh?.()
    } catch (e) { alertOnce(e.message) }
    finally { setQueuing(false) }
  }

  const handleBulkHide = (hidden) =>
    bulkEach(selectedInView.map((l) => l.lot_id), (id) => setHidden(id, hidden))
  const handleBulkWatch = (watched) =>
    bulkEach(selectedInView.map((l) => l.lot_id), (id) => setWatch(id, watched))

  const smallBtn = { fontSize: 13, padding: '4px 9px' }
  const aiCheck = (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: 13 }}>
      <button onClick={handleEnrichMatching} disabled={!aiTargets.length || queuing}
              title="AI reads the photo and listing for condition and a closer identification, then prices again. Only lots already priced at or above the amount, most valuable first (asks first, shows cost)">
        AI check {aiTargets.length.toLocaleString()} worth
      </button>
      $<input type="number" min="0" value={aiMin}
              onChange={(ev) => setAiMin(ev.target.value)}
              title="Only lots priced at or above this"
              style={{ width: 56 }} />+
    </span>
  )
  const bulkBar = selectedInView.length > 0 && (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, alignItems: 'center',
                  margin: '6px 0', padding: 6, borderRadius: 6,
                  border: '1px solid var(--border, #555)' }}>
      <strong style={{ marginRight: 4 }}>{selectedInView.length.toLocaleString()} selected</strong>
      {!allSelected(selected, sorted) && (
        <button style={smallBtn} onClick={() => setSelected(selectAll(sorted))}
                title="Select every lot matching the current filters — the whole result, not just the rows on screen">
          Select all {sorted.length.toLocaleString()}
        </button>
      )}
      <button className="primary" style={smallBtn} onClick={handleBulkComps} disabled={queuing}
              data-track="Comps only (selected lots)"
              title="Sold-comps lookup on the selected lots' titles as-is. No AI cost (asks first, shows the request count)">
        Price with comps (no AI)
      </button>
      <button style={smallBtn} onClick={handleBulkPrice} disabled={queuing}
              title="AI reads each selected lot's photo and listing for condition and a closer identification, then prices it (asks first, shows cost)">
        AI check selected
      </button>
      <button style={smallBtn} onClick={() => handleBulkHide(true)}>Hide</button>
      <button style={smallBtn} onClick={() => handleBulkHide(false)}>Unhide</button>
      <button style={smallBtn} onClick={() => handleBulkWatch(true)}>Watch</button>
      <button style={smallBtn} onClick={() => handleBulkWatch(false)}>Unwatch</button>
      <button style={smallBtn} onClick={() => setSelected(new Set())}>Clear selection</button>
    </div>
  )

  if (!lots.length) return <p>No lots yet — scan auctions and import one above.</p>

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
            value={MOBILE_SORTS.findIndex((s) => s.key === sort.key && s.dir === sort.dir)}
            onChange={(ev) => {
              const s = MOBILE_SORTS[Number(ev.target.value)] ?? MOBILE_SORTS[0]
              setSort({ key: s.key, dir: s.dir })
            }}
            style={{ flex: 1, padding: 6, fontSize: 14, maxWidth: '48%' }}
          >
            {MOBILE_SORTS.map((s, i) => <option key={s.label} value={i}>{s.label}</option>)}
          </select>
          {/* Every filterable column, generated from the same list the
              desktop header uses - the phone used to hand-maintain a subset
              with no bid, cost, resale, max-bid, ROI or auction filter. The
              title search above already binds the one text column. */}
          <button style={{ flex: '1 1 100%', padding: 8, fontSize: 14 }}
                  onClick={() => setShowFilters((v) => !v)}
                  aria-expanded={showFilters}>
            {showFilters ? 'Hide filters' : `Filters${activeFilterCount ? ` (${activeFilterCount} on)` : ''}`}
          </button>
          {showFilters && COLUMNS.filter((c) => c.filter && c.filter !== 'text').map((c) => (
            <select
              key={c.key}
              value={colFilters[c.key] ?? ''}
              onChange={(ev) => setFilter(c.key, ev.target.value)}
              style={{ flex: '1 1 47%', padding: 6, fontSize: 14, maxWidth: '48%' }}
            >
              <option value="">{c.label}: all</option>
              {presetsFor(c, distinctValues).map((o) => (
                <option key={o.value} value={o.value}>{o.label}</option>
              ))}
            </select>
          ))}
          {activeFilterCount > 0 && (
            <button style={{ flex: '1 1 100%', padding: 6, fontSize: 13 }}
                    onClick={() => setColFilters({})}>
              Clear {activeFilterCount} filter{activeFilterCount === 1 ? '' : 's'}
            </button>
          )}
          <button className="primary" onClick={handleCompsMatching}
                  disabled={!unpricedShown.length || queuing}
                  title="The default: sold comps for every unpriced lot matching the filters, on each lot's own title. No AI cost."
                  style={{ flex: '1 1 100%', padding: 10, fontSize: 15 }}>
            {queuing ? <><span className="spinner" />Queuing…</>
                     : `Price ${unpricedShown.length.toLocaleString()} with comps (no AI)`}
          </button>
          {aiCheck}
          {!selectedInView.length && sorted.length > 0 && (
            <button style={{ flex: '1 1 100%', padding: 6, fontSize: 13 }}
                    onClick={() => setSelected(selectAll(sorted))}
                    title="Select every lot matching the current filters, then act on them together">
              Select all {sorted.length.toLocaleString()}
            </button>
          )}
          {bulkBar && <div style={{ flexBasis: '100%' }}>{bulkBar}</div>}
          {anyQueued && <span style={{ flexBasis: '100%' }}><span className="spinner" />{lots.filter((l) => l.enrichment?.status === 'queued').length} lots in the queue… updates when they finish</span>}
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
                <input type="checkbox"
                       checked={selected.has(lot.lot_id)}
                       onChange={() => setSelected(toggle(selected, lot.lot_id))}
                       title="Select this lot for a bulk action"
                       style={{ marginRight: 6, transform: 'scale(1.2)' }} />
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
                  {lot.hidden ? 'show' : 'hide'}
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
                {(lot.item_closed ?? lot.auction_closed) ? 'closed · ' : ''}
                {lot.auction_no_us_ship && (
                  <span style={{ color: 'var(--danger)' }}
                        title="This house has said it won't ship into the US">no US shipping · </span>
                )}
                <span onClick={() => onSelectAuction?.(lot.auction_id)}
                      title="Show only this auction's items"
                      style={{ cursor: 'pointer', textDecoration: 'underline dotted' }}>
                  {lot.auction_name}
                </span>
              </div>
              {e.enriched_title && e.enriched_title !== lot.title && (
                <div style={{ color: 'var(--muted)', fontSize: 13 }}>→ {e.enriched_title}</div>
              )}
              {e.fraud_note && (
                <div style={{ color: 'var(--warn)', fontSize: 12 }}
                     title="Funko fraud check: how the listing's own words changed the pricing">
                  fraud check: {e.fraud_note}
                </div>
              )}
              {e.identity_note && (
                <div style={{ color: 'var(--danger)', fontSize: 12 }}
                     title="The AI's title asserts something the listing never said, so the value came from the listing's own title and the gold badge is withheld. Correct the title to confirm what it is.">
                  identity uncertain: AI added {e.identity_note} — not in the listing
                </div>
              )}
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px 14px', fontSize: 14, margin: '6px 0' }}>
                <span style={closesIn(lot.closes_at, now).urgent
                             ? { color: 'var(--danger)', fontWeight: 700 } : undefined}>
                  closes {closesIn(lot.closes_at, now).text}
                </span>
                <span>Bid {money(lot.current_bid)} / {money(lot.next_bid)}</span>
                <span>Cost {money(lot.est_cost)}</span>
                <span title={e.gold_check === 'demoted' ? e.gold_check_note : undefined}
                      style={e.gold_check === 'demoted'
                        ? { textDecoration: 'line-through', opacity: 0.6 } : undefined}>
                  Resale {money(e.est_resale)}{e.comp_count > 0 ? ` (${e.comp_count})` : ''}
                </span>
                {e.gold_check === 'demoted' && (
                  <span style={{ color: '#e05555', fontSize: 12 }}> rejected by audit</span>
                )}
                {houseEstimate(lot) && (
                  <span style={{ color: 'var(--muted)' }}
                        title={houseRatioTitle(lot.house_ratio, lot.house_ratio_n) || undefined}>
                    house {houseEstimate(lot)}
                    {houseRatioLabel(lot.house_ratio, lot.house_ratio_n)
                      ? ` · ${houseRatioLabel(lot.house_ratio, lot.house_ratio_n)}` : ''}
                  </span>
                )}
                <span>Max bid {money(e.max_bid)}</span>
                {e.est_roi != null && (
                  <span style={{ fontWeight: 600,
                                 color: Number(e.est_roi) < 0 ? 'var(--danger)'
                                   : e.roi_status === 'GOLD MINE' ? 'var(--success)' : undefined }}>
                    ROI {Math.round(Number(e.est_roi) * 100)}%
                  </span>
                )}
              </div>
              {e.est_resale != null && <CompsPeek lot={lot} e={e} />}
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginBottom: 6 }}>
                <span className="badge">{lot.logistics_ease}</span>
                {e.bolo_brand && (
                  <span className="badge bolo">BOLO: {e.bolo_brand} T{e.bolo_tier ?? '?'}</span>
                )}
                {e.auth_required && (
                  <span className="badge bolo"
                        title="Luxury-brand match — resale value depends on authentication; don't trust the comps until verified">
                    authenticate first
                  </span>
                )}
                {e.verdict && (
                  <span className="badge"
                        title={e.gold_check_note || e.roi_reason || undefined}
                        style={(e.gold_check_note || e.roi_reason) ? { cursor: 'help' } : undefined}>
                    {gold ? (e.gold_check === 'confirmed' || e.gold_check === 'corrected' ? 'GOLD ✓' : 'GOLD')
                      : ''} {e.verdict}
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
                        title="Work out what this is worth: match it against your BOLO brand list, have AI read the description (or the photo) to identify it and judge condition, look up eBay comps, then compute your max bid and ROI."
                        onClick={() => handleEnrich(lot.lot_id)}>Price it</button>
                <button style={{ flex: 1, padding: 8 }} disabled={isWorking(lot)}
                        title="For a box of many things (a crate of CDs, a tray of tools): AI reads the full-size photo, lists every item it can identify, prices them one by one and totals them. Slower and dearer than pricing the lot as a single item."
                        onClick={() => handleInspect(lot.lot_id)}>Price each item</button>
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
      <button className="primary" onClick={handleCompsMatching}
              disabled={!unpricedShown.length || queuing}
              title="The default: sold comps for every unpriced lot matching the filters, on each lot's own title. No AI cost.">
        {queuing ? <><span className="spinner" />Queuing…</>
                 : `Price ${unpricedShown.length.toLocaleString()} with comps (no AI)`}
      </button>
      <span style={{ marginLeft: 8 }}>{aiCheck}</span>
      {anyQueued && <span style={{ marginLeft: '0.75rem' }}><span className="spinner" />{lots.filter((l) => l.enrichment?.status === 'queued').length} lots in the queue… updates when they finish</span>}
      <span style={{ marginLeft: '0.75rem' }}>{countLine}</span>
    </div>
    {bulkBar}
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
          <th style={{ width: 28 }}
              title={`Select all ${sorted.length.toLocaleString()} lots matching the current filters`}>
            <input type="checkbox"
                   title="Select every lot in view"
                   checked={allSelected(selected, sorted)}
                   onChange={(ev) => setSelected(ev.target.checked ? selectAll(sorted) : new Set())} />
          </th>
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
          <th></th>
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
                  {presetsFor(c, distinctValues).map((o) => (
                    <option key={o.value} value={o.value}>{o.label}</option>
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
              <td style={cell}>
                <input type="checkbox"
                       checked={selected.has(lot.lot_id)}
                       onChange={() => setSelected(toggle(selected, lot.lot_id))}
                       title="Select this lot for a bulk action" />
              </td>
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
                  {lot.hidden ? 'show' : 'hide'}
                </button>
                {e.bolo_brand && (
                  <span className="badge bolo" style={{ cursor: 'help', marginRight: 4 }}
                        title={`BOLO match: ${e.bolo_brand} (tier ${e.bolo_tier ?? '?'})`}>BOLO</span>
                )}
                {e.auth_required && (
                  <span className="badge bolo" style={{ cursor: 'help', marginRight: 4 }}
                        title="Luxury/precious-metal match — resale depends on authentication; don't trust the comps until verified in hand">verify</span>
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
                {e.fraud_note && (
                  <div style={{ color: 'var(--warn)', fontSize: 12 }}
                       title="Funko fraud check: how the listing's own words changed the pricing">
                    fraud check: {e.fraud_note}
                  </div>
                )}
                {e.identity_note && (
                  <div style={{ color: 'var(--danger)', fontSize: 12 }}
                       title="The AI's title asserts something the listing never said, so the value came from the listing's own title and the gold badge is withheld. Click the title above to correct it and confirm what it is.">
                    identity uncertain: AI added {e.identity_note} — not in the listing
                  </div>
                )}
                {e.notes && e.ai_source === 'vision-itemized' && (
                  <details style={{ fontSize: 12, color: 'var(--muted)' }}>
                    <summary>itemized breakdown</summary>
                    <pre style={{ whiteSpace: 'pre-wrap', margin: 0 }}>{e.notes}</pre>
                  </details>
                )}
              </td>
              <td style={{ ...cell, fontSize: 12, maxWidth: 140 }}>
                {(lot.item_closed ?? lot.auction_closed) && <div><strong>closed</strong></div>}
                {lot.auction_no_us_ship && (
                  <div style={{ color: 'var(--danger)' }}
                       title="This house has said it won't ship into the US">no US shipping</div>
                )}
                <span onClick={() => onSelectAuction?.(lot.auction_id)}
                      title="Show only this auction's items"
                      style={{ cursor: 'pointer', textDecoration: 'underline dotted' }}>
                  {lot.auction_name}
                </span>
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
                {ev && (
                  <div title={(EVIDENCE_NOTE[ev] || '')
                    + (e.comp_count ? ` (${e.comp_count} comps)` : '')}
                       style={{ color: isPaleEvidence(ev) ? '#e0a030' : 'var(--muted)',
                                fontSize: 11, cursor: 'help' }}>
                    {EVIDENCE_LABEL[ev] || ev}
                  </div>
                )}
                {/* The audit rejected this number and had nothing to put in
                    its place. Showing it unmarked reads as a real estimate,
                    which is how a debunked $370 stayed on screen next to the
                    note explaining it was wrong. */}
                {e.gold_check === 'demoted' && (
                  <div title={e.gold_check_note
                    || 'The second-opinion audit judged this value implausible'}
                       style={{ color: '#e05555', fontSize: 11, fontWeight: 600,
                                cursor: 'help' }}>
                    rejected by audit
                  </div>
                )}
                {houseEstimate(lot) && (
                  <div style={{ color: 'var(--muted)', fontSize: 11 }}
                       title={houseRatioTitle(lot.house_ratio, lot.house_ratio_n)
                         || "The auction house's own estimate range — promotional, but weak-evidence values are capped against it"}>
                    house {houseEstimate(lot)}
                    {houseRatioLabel(lot.house_ratio, lot.house_ratio_n)
                      ? ` · ${houseRatioLabel(lot.house_ratio, lot.house_ratio_n)}` : ''}
                  </div>
                )}
                {e.est_resale != null && <CompsPeek lot={lot} e={e} />}
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
                    : e.gold_check === 'corrected'
                      ? `AI replaced the comp value with its own — ${e.gold_check_note || 'see price source'}`
                      : e.gold_check === 'demoted'
                        ? `AI demoted this gold — ${e.gold_check_note || 'value judged implausible'}`
                        : e.roi_reason || undefined}
                  style={(e.gold_check || e.roi_reason) ? { cursor: 'help' } : undefined}>
                  {gold ? (e.gold_check === 'confirmed' || e.gold_check === 'corrected' ? 'GOLD ✓' : 'GOLD')
                    : ''}
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
                <button disabled={isWorking(lot)} title="Work out what this is worth: match it against your BOLO brand list, have AI read the description (or the photo) to identify it and judge condition, look up eBay comps, then compute your max bid and ROI."
                        onClick={() => handleEnrich(lot.lot_id)}>Price it</button>{' '}
                <button disabled={isWorking(lot)} title="For a box of many things (a crate of CDs, a tray of tools): AI reads the full-size photo, lists every item it can identify, prices them one by one and totals them. Slower and dearer than pricing the lot as a single item."
                        onClick={() => handleInspect(lot.lot_id)}>
                  Price each item
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
