import { Fragment, useEffect, useMemo, useRef, useState } from 'react'
import { ask } from '../lib/ask'
import NumberField from './NumberField'
import { notify } from '../lib/notice'
import { enrichLot, compsLot, recheckLot, fetchLot, patchEnrichment, flagComp, enrichBatch, repriceSelected, setWatch, setHidden, hideLike, refreshBidsForLots, analyzeShippingForLots, alertOnce, parseUtc } from '../api'
import { aiDone, rowAction } from '../lib/pricing'
import { PAGE_SIZES, pageButtons, pageWindow, savePageSize, savedPageSize, searchMatches, showingText } from '../lib/paging'
import { compRows, ebaySoldUrl } from '../lib/comps'
import { houseRatioLabel, houseRatioTitle } from '../lib/calibration'
import { CLOSING_RANGES, ROI_RANGES, hoursUntil, matchesFilter, presetsFor, roiPercent } from '../lib/filters'
import { allSelected, chunked, inView, selectAll, toggle } from '../lib/selection'
import { track } from '../lib/track'
import { offerFrom, offerLabel } from '../lib/hideLike'
import { ADDON_FLOOR_KEY, ARRANGE_KEY, basketLabel, groupByAuction, savePref, savedAddonFloor, savedArrange } from '../lib/grouping'
import DriveFrom, { CarIcon, formatDrive } from './DriveFrom'
import useMediaQuery from '../useMediaQuery'

// The homework behind a resale number: the comp records the pricer actually
// used (raw observed prices — never scaled to match the conclusion), plus a
// one-tap eBay sold-listings search so distrust costs thirty seconds, not a
// Google detour. Rows enriched before comps were stored still get the link.
function CompsPeek({ lot, e, onLotUpdated, label, labelStyle }) {
  const search = ebaySoldUrl(e.enriched_title || lot.title)
  // The list endpoint leaves the comp records out - they were 60% of its
  // payload and are read only here - so the first open fetches this one
  // lot, which always carries them.
  const [comps, setComps] = useState(e.comps ?? null)
  const [loading, setLoading] = useState(false)
  const [flagging, setFlagging] = useState(false)
  const rows = compRows(comps)
  const expected = e.comp_count ?? 0
  if (!search && !rows.length && !expected) return null

  async function toggleFlag() {
    if (flagging) return
    setFlagging(true)
    try {
      let note
      if (!e.comp_flagged) {
        note = window.prompt(
          "What's wrong with these comps? (optional — Cancel still flags it)") || undefined
      }
      const updated = await flagComp(lot.lot_id, { flagged: !e.comp_flagged, note })
      onLotUpdated(updated)
    } catch (err) { alertOnce(err.message) } finally { setFlagging(false) }
  }

  async function fill(ev) {
    if (!ev.target.open || comps || loading || !expected) return
    setLoading(true)
    try {
      const full = await fetchLot(lot.lot_id)
      setComps(full.enrichment?.comps ?? [])
    } catch {
      setComps([])          // the link below still works
    } finally {
      setLoading(false)
    }
  }

  return (
    <details style={{ marginTop: 2 }} onToggle={fill} className={label ? 'peek' : undefined}>
      <summary style={{ color: 'var(--muted)', fontSize: 11, cursor: 'pointer', ...labelStyle }}
               title={label ? 'Show the comps behind this price' : undefined}>
        {label ?? <>evidence{(rows.length || expected) ? ` (${rows.length || expected})` : ''}</>}
        {e.comp_flagged ? ' — flagged wrong' : ''}
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
        {e.comp_flagged && (
          <div style={{ marginBottom: 3, color: 'var(--danger)' }}>
            flagged: wrong comps{e.comp_flag_note ? ` — ${e.comp_flag_note}` : ''}
          </div>
        )}
        <button type="button" className="link-like" disabled={flagging}
                style={{ fontSize: 11, marginBottom: 3, display: 'block' }}
                onClick={toggleFlag}
                title="Mark this valuation as wrong — builds a worklist for fixing the comp matching">
          {flagging ? 'saving…' : e.comp_flagged ? 'unflag' : 'flag these comps as wrong'}
        </button>
        {rows.map((r, i) => (
          <div key={i} style={{ marginBottom: 2 }}>
            {r.label}
            {' — '}
            {r.url
              ? <a href={r.url} target="_blank" rel="noreferrer">{r.title}</a>
              : r.title}
          </div>
        ))}
        {loading && (
          <div style={{ color: 'var(--muted)', marginBottom: 2 }}>
            <span className="spinner" /> loading the comps…
          </div>
        )}
        {!rows.length && !loading && (
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
const USD = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' })
function money(v) {
  if (v === null || v === undefined) return '—'
  return USD.format(Number(v))
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
  if (e.est_resale != null) return 'priced by comps'
  return 'not priced'
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
  // Set once an edit is saved or cancelled, so the blur that follows
  // Enter or Escape doesn't save a second time.
  const done = useRef(false)

  function start() {
    done.current = false
    setDraft(rawValue ?? '')
    setEditing(true)
  }

  function cancel() {
    done.current = true
    setEditing(false)
  }

  // Clicking or tabbing away keeps what was typed - it used to throw it
  // away, which lost corrections without a word.
  async function save(value) {
    if (done.current) return
    done.current = true
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
          onKeyDown={(ev) => { if (ev.key === 'Escape') cancel() }}
          onBlur={cancel}
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
          if (ev.key === 'Escape') cancel()
        }}
        onBlur={() => save(draft)}
        style={{ width: inputType === 'number' ? 70 : 160, fontSize: 13 }}
      />
    )
  }
  return (
    <button
      type="button"
      className="edit-cell"
      onClick={start}
      data-track="Edit a value (click to correct)"
      title="Click to correct — your value is remembered and won't be overwritten"
    >
      {display}{edited ? ' ✎' : ''}
    </button>
  )
}

// Mobile sort choices — a dropdown replaces click-to-sort headers on phones.
// Each view's search, column filters and sort, kept while the app is open:
// the table remounts when the view changes, and picks its own back up.
const VIEW_TABLE_STATE = new Map()
const SORT_KEY = 'auctionscout.sort'
const FLIPPED_KEY = 'auctionscout.flippedGroups'
function savedSort(viewKey) {
  try {
    const v = JSON.parse(window.localStorage.getItem(`${SORT_KEY}.${viewKey}`))
    return v && typeof v.key === 'string' && (v.dir === 1 || v.dir === -1) ? v : null
  } catch { return null }
}
function savedFlipped(storage, viewKey) {
  try {
    const v = JSON.parse(storage?.getItem(`${FLIPPED_KEY}.${viewKey}`))
    return new Set(Array.isArray(v) ? v : [])
  } catch { return new Set() }
}

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

export default function LotTable({ lots, onLotUpdated, onRefresh, onLotTouched,
                                  onSelectAuction, auctions = {},
                                  driveFrom = null, driveAvailable = false,
                                  onSaveDriveFrom, onClearDriveFrom,
                                  toolbar = null, toolbarEnd = null, panel = null,
                                  toolbarAllLots = null, onImportMissing,
                                  loadedByAuction = {}, onShowFilters,
                                  phoneFilters = null, phoneBehindFilters = null,
                                  filtersOpen = false, viewKey = 'items', onSetLive,
                                  liveView = false }) {
  const remembered = VIEW_TABLE_STATE.get(viewKey) || {}
  const isMobile = useMediaQuery('(max-width: 768px)')
  const [pollingIds, setPollingIds] = useState(new Set())
  // Row menus (the ⋯ at the end of a row) are <details>: close any open one
  // on a click outside it or on Escape, the way a menu is expected to.
  useEffect(() => {
    const closeOpen = (except) => document.querySelectorAll('details.row-menu[open], details.picker[open]')
      .forEach((d) => { if (d !== except) d.open = false })
    const onClick = (ev) => closeOpen(ev.target.closest?.('details.row-menu, details.picker'))
    const onKey = (ev) => { if (ev.key === 'Escape') closeOpen(null) }
    document.addEventListener('click', onClick)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('click', onClick)
      document.removeEventListener('keydown', onKey)
    }
  }, [])
  // Countdown clock — a 30s tick keeps every "closes in" cell live.
  const [now, setNow] = useState(Date.now())
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 30000)
    return () => clearInterval(t)
  }, [])
  // Best return first is the default view — that's the question the app
  // exists to answer. Click any header (or the mobile Sort menu) to change it.
  // The Live view reads in the order the auction closes its lots.
  // The usage log had Watched re-sorted by Closes after nearly every load:
  // it starts there now, and every view keeps its sort across reloads.
  const [sort, setSort] = useState(() => remembered.sort ?? savedSort(viewKey)
    ?? (liveView ? { key: 'lot_number', dir: 1 }
      : viewKey === 'watched' ? { key: 'closes', dir: 1 } : { key: 'roi', dir: -1 }))
  const [colFilters, setColFilters] = useState(remembered.colFilters ?? {})
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
  // Pages, as DataTables does them: all lots stay loaded for filtering and
  // sorting; only one page is painted, which is what keeps thousands of
  // rows from eating browser memory. The size is a per-browser preference.
  const [pageSize, setPageSizeState] = useState(() =>
    savedPageSize(typeof window === 'undefined' ? null : window.localStorage))
  const [page, setPage] = useState(1)
  const setPageSize = (n) => {
    setPageSizeState(n)
    savePageSize(n, typeof window === 'undefined' ? null : window.localStorage)
  }
  // One search box across title, AI title, auction, category and lot number.
  const [search, setSearch] = useState(remembered.search ?? '')
  useEffect(() => {
    VIEW_TABLE_STATE.set(viewKey, { sort, colFilters, search })
  }, [viewKey, sort, colFilters, search])
  useEffect(() => {
    savePref(storage, `${SORT_KEY}.${viewKey}`, JSON.stringify(sort))
  }, [viewKey, sort])
  // "By auction" groups lots under their sale, because a pickup trip is a
  // fixed cost: once you're going for a gold mine, a weaker lot at the same
  // auction is worth adding. Both the view and the add-on bar are
  // per-browser preferences, like the page size.
  const storage = typeof window === 'undefined' ? null : window.localStorage
  const [arrangePref, setArrangeState] = useState(() => savedArrange(storage))
  // The Live view is always by auction - it is a set of auctions.
  const arrange = liveView ? 'auction' : arrangePref
  const setArrange = (v) => { setArrangeState(v); savePref(storage, ARRANGE_KEY, v) }
  const [addonFloor, setAddonFloorState] = useState(() => savedAddonFloor(storage))
  const setAddonFloor = (n) => { setAddonFloorState(n); savePref(storage, ADDON_FLOOR_KEY, n) }
  // Auctions whose other lots are unfolded, and how many of them to show.
  const [openGroups, setOpenGroups] = useState(() => new Map())
  // Auctions the user has opened or closed by clicking the header, flipped
  // from their default: one with a gold mine starts open, one without starts
  // closed.
  // Remembered across reloads, per view: the log had 39 one-at-a-time
  // opens and closes in a day and a half, many of them redoing the last
  // session's.
  const [flippedGroups, setFlippedGroups] = useState(() => savedFlipped(storage, viewKey))
  useEffect(() => {
    savePref(storage, `${FLIPPED_KEY}.${viewKey}`, JSON.stringify([...flippedGroups]))
  }, [storage, viewKey, flippedGroups])
  // True while the enrich-batch request is in flight.
  const [queuing, setQueuing] = useState(false)
  // Multi-select: lot_ids the user has ticked. Kept as a Set of ids rather
  // than row indexes so ticks survive re-sorting, re-filtering and the
  // reload when a batch finishes. Actions act on the ticked lots
  // still in the current result (see lib/selection.inView).
  const [selected, setSelected] = useState(() => new Set())
  // The "more like this" offer raised by the last hide, and the product keys
  // already answered - a ref, because saying no to one changes nothing on
  // screen and should not re-render the table.
  const [likeOffer, setLikeOffer] = useState(null)
  const dismissedKeys = useRef(new Set())
  // Rows with a request already on the way. A ref, not state, because the
  // guard has to be right for two clicks landing inside one render.
  const inFlight = useRef(new Set())

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
    if (!active.length && !search.trim()) return lots
    // filterGet lets a column sort by one value and filter by another - the
    // Closes column sorts by the instant but filters by hours until close.
    return lots.filter((l) => searchMatches(l, search) && active.every((c) =>
      matchesFilter((c.filterGet ?? c.get)(l), colFilters[c.key].trim())))
  }, [lots, colFilters, search])

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

  const groups = useMemo(
    () => (arrange === 'auction'
      ? groupByAuction(sorted, { auctions, addonFloorPct: addonFloor, isOverbid })
      : []),
    [arrange, sorted, auctions, addonFloor])

  // A new result - filters, search, sort or page size changed - starts on
  // page one, not wherever the old one was.
  useEffect(() => { setPage(1) }, [colFilters, search, sort, pageSize])

  function handleSearch(value) {
    pinnedPos.current.clear()
    clearTimeout(filterLogTimers.current.search)
    if (value?.trim()) {
      filterLogTimers.current.search = setTimeout(
        () => track('filter', { key: 'search', value: String(value).slice(0, 40) }), 1200)
    }
    setSearch(value)
  }

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

  // The row button turns to "Working…" off pollingIds, and both of these
  // used to add the lot to it only AFTER the request came back. For the
  // length of that round trip the button still read "Further inspect with
  // AI" and was still live, so a press looked like it had done nothing.
  //
  // The usage log is full of the consequence: 98 presses of this button,
  // 47% of them less than five seconds apart and nine pairs in the SAME
  // second. A second press on a lot the worker has already claimed clears
  // claimed_at and re-queues it, so the AI runs twice and is paid for
  // twice.
  //
  // So the lot is claimed here, before the await, and released again if
  // the request fails. The ref is what the guard reads: two clicks inside
  // one render both see the same stale state Set, while a ref is updated
  // the moment the first one runs.
  function claimRow(lotId) {
    if (inFlight.current.has(lotId)) return false
    inFlight.current.add(lotId)
    setPollingIds((prev) => new Set(prev).add(lotId))
    pinLot(lotId)
    return true
  }

  function releaseRow(lotId) {
    inFlight.current.delete(lotId)
    setPollingIds((prev) => {
      const next = new Set(prev)
      next.delete(lotId)
      return next
    })
  }

  async function handleEnrich(lotId) {
    if (!claimRow(lotId)) return
    try {
      await enrichLot(lotId)
      poll(lotId)
    } catch (e) {
      releaseRow(lotId)
      alertOnce(e.message)
    }
  }

  async function handleComps(lotId) {
    if (!claimRow(lotId)) return
    try {
      await compsLot(lotId)
      poll(lotId)
    } catch (e) {
      releaseRow(lotId)
      alertOnce(e.message)
    }
  }

  // The lock is the point, so getting past it costs a confirmation and
  // works on exactly one lot.
  async function handleRecheck(lotId) {
    // Claimed before the question, so a second press can't open a second one.
    if (!claimRow(lotId)) return
    if (!(await ask('This lot was already priced by AI.\n\nRe-checking it '
                    + 'spends another AI call on this one lot.', { confirmLabel: 'Re-check with AI' }))) {
      releaseRow(lotId)
      return
    }
    try {
      await recheckLot(lotId)
      poll(lotId)
    } catch (e) {
      releaseRow(lotId)
      alertOnce(e.message)
    }
  }

  // The row's one button: comps if unpriced, AI if comps-priced, locked
  // once AI has priced it.
  function rowButton(lot, style) {
    const a = rowAction(lot.enrichment || {}, pollingIds.has(lot.lot_id))
    const onClick = a.step === 'comps' ? () => handleComps(lot.lot_id)
      : a.step === 'ai' ? () => handleEnrich(lot.lot_id) : undefined
    const button = (
      <button style={style} disabled={a.disabled} title={a.title} onClick={onClick}
              data-track={a.step === 'comps' ? 'Price with comps (row)'
                : a.step === 'ai' ? 'Further inspect with AI (row)' : undefined}>
        {a.label}
      </button>
    )
    // A locked lot keeps its disabled button, so the guard stays visible;
    // the way past it is a separate, quieter control that asks first.
    if (a.step !== 'locked') return button
    return (
      <>
        {button}
        <button type="button" className="link-like"
                style={{ fontSize: 12, marginTop: 4 }}
                onClick={() => handleRecheck(lot.lot_id)}
                data-track="Re-check with AI (row)"
                title="Release the lock on this one lot and price it again. Spends another AI call.">
          re-check
        </button>
      </>
    )
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

  // One decision instead of nine. A sale lists the same product many times
  // and tells the copies apart with an asset tag, which is why hiding was
  // the most-used action and came in bursts. Counts first, then asks.
  async function handleHideLike(lotId, hidden) {
    try {
      const peek = await hideLike(lotId, hidden, true)
      if (peek.key === null) { alertOnce(peek.reason); return }
      if (peek.changed === 0) { alertOnce('Nothing else matches this one.'); return }
      const verb = hidden ? 'Hide' : 'Bring back'
      const names = peek.titles.slice(0, 3).join('; ')
      const more = peek.changed > 3 ? ' and more' : ''
      if (!await ask(
        `${verb} ${peek.changed} lot${peek.changed === 1 ? '' : 's'} matching `
        + `"${peek.key}"? ${names}${more}`)) return
      await hideLike(lotId, hidden, false)
      onRefresh?.()
    } catch (e) { alertOnce(e.message) }
  }

  async function handleHide(lotId, hidden) {
    try {
      const updated = await setHidden(lotId, hidden)
      onLotUpdated(updated)
      if (hidden) offerTheRest(lotId, updated)
    } catch (e) { alertOnce(e.message) }
  }

  // Hiding is 21% of every button press here, and half of those presses land
  // in unbroken runs of three or more - the same product dismissed over and
  // over, because a liquidation sale lists it once per asset tag. The
  // one-click version of that has existed for a while as a dim "+ all" beside
  // the hide button, and in five days it was pressed once against 131 hides.
  //
  // So it asks AFTER the click instead of hoping to be noticed before it:
  // the offer appears where the decision was just made, about a product the
  // user has this second proved they do not want. Nothing is hidden without
  // a second press.
  async function offerTheRest(lotId, lot) {
    try {
      const offer = offerFrom(await hideLike(lotId, true, true),
                              dismissedKeys.current)
      if (offer) setLikeOffer({ lotId, ...offer })
    } catch {
      // The hide itself worked. A failed offer is a missing bonus, not an
      // error worth a popup.
    }
  }

  async function acceptLikeOffer() {
    const offer = likeOffer
    if (!offer) return
    setLikeOffer(null)
    try {
      await hideLike(offer.lotId, true, false)
      onRefresh?.()
    } catch (e) { alertOnce(e.message) }
  }

  function dismissLikeOffer() {
    // Answered once is answered. Asking again about a product the user has
    // just said no to is how a helpful prompt becomes a nag.
    if (likeOffer) dismissedKeys.current.add(likeOffer.key)
    setLikeOffer(null)
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
      if (updated.enrichment?.status !== 'queued') {
        releaseRow(lotId)   // clears the ref too, or the row never unlocks
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
  // Priced, not yet AI-checked, not queued: what the threshold below could
  // reach at ANY setting. Deciding whether to show the control at all off
  // this, rather than off aiTargets, is what keeps the threshold input
  // reachable when the current setting happens to match nothing.
  const aiReachable = sorted
    .filter((l) => !aiDone(l.enrichment) && l.enrichment?.status !== 'queued'
                   && l.enrichment?.est_resale != null)
  const aiTargets = sorted
    .filter((l) => !aiDone(l.enrichment) && l.enrichment?.status !== 'queued'
                   && l.enrichment?.est_resale != null
                   && Number(l.enrichment.est_resale) >= Number(aiMin || 0))
    .sort((a, b) => Number(b.enrichment.est_resale) - Number(a.enrichment.est_resale))

  async function handleCompsMatching() {
    if (!unpricedShown.length || queuing) return
    const ok = await ask(
      `Look up sold comps for the ${unpricedShown.length} unpriced lots matching your filters?\n\n` +
      `No AI cost - at most one SoldComps request per title (identical titles share one). ` +
      `Progress appears in the bar at the top of the page.`)
    if (!ok) return
    setQueuing(true)
    try {
      const r = await repriceSelected(unpricedShown.map((l) => l.lot_id))
      if (r.already_running) notify('A re-price is already running; let it finish first.')
      onRefresh?.()
    } catch (e) { alertOnce(e.message) }
    finally { setQueuing(false) }
  }

  // One auction's lots that "Further inspect with AI" would run on: priced,
  // not yet AI-checked, not already queued, still open - the same rule the
  // row button follows. Most valuable first, as the bulk check does.
  const auctionAiTargets = (g) => g.lots
    .filter((l) => rowAction(l.enrichment || {}, pollingIds.has(l.lot_id)).step === 'ai'
                   && !(l.item_closed ?? l.auction_closed))
    .sort((a, b) => Number(b.enrichment?.est_resale || 0) - Number(a.enrichment?.est_resale || 0))

  // One auction's lots the free comps pricing would run on: no value yet,
  // not queued, still open - what the row's "Price with comps" offers.
  // The usage log's second-biggest click run was that row button, pressed
  // up to eight times in a row through one auction.
  const auctionUnpriced = (g) => g.lots
    .filter((l) => rowAction(l.enrichment || {}, pollingIds.has(l.lot_id)).step === 'comps'
                   && !(l.item_closed ?? l.auction_closed))

  async function handlePriceAuction(g) {
    const targets = auctionUnpriced(g)
    if (!targets.length || queuing) return
    const ok = await ask(
      `Look up sold comps for the ${targets.length} unpriced `
      + `${targets.length === 1 ? 'lot' : 'lots'} at ${g.name}?\n\n`
      + `No AI cost - at most one SoldComps request per title, soonest-closing first. `
      + `Progress appears in the bar at the top of the page.`)
    if (!ok) return
    setQueuing(true)
    try {
      const r = await repriceSelected(targets.map((l) => l.lot_id))
      if (r.already_running) notify('A re-price is already running; let it finish first.')
      onRefresh?.()
    } catch (e) {
      alertOnce(e.message)
    } finally {
      setQueuing(false)
    }
  }

  async function handleEnrichAuction(g) {
    const targets = auctionAiTargets(g)
    if (!targets.length || queuing) return
    const cost = (targets.length * 0.005).toFixed(2)
    const ok = await ask(
      `Further inspect with AI the ${targets.length} priced ${targets.length === 1 ? 'lot' : 'lots'} `
      + `at ${g.name} that haven't had one?\n\n`
      + `AI reads each one's photo and listing for condition and a closer identification, `
      + `then prices it again - roughly $${cost} of API usage, most valuable first.\n\n`
      + `Progress appears in the bar at the top of the page.`)
    if (!ok) return
    setQueuing(true)
    try {
      const r = await enrichBatch(targets.map((l) => l.lot_id))
      onRefresh?.()
      if (!r.queued) notify('Nothing to queue - those lots are already inspected or in progress.')
    } catch (e) {
      alertOnce(e.message)
    } finally {
      setQueuing(false)
    }
  }

  async function handleEnrichMatching() {
    if (!aiTargets.length || queuing) return
    const cost = (aiTargets.length * 0.005).toFixed(2)
    const ok = await ask(
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
        notify('Nothing to queue — those lots are already enriched or in progress.')
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
    if (!ids.length) { notify('Every selected lot is already priced or in progress.'); return }
    const cost = (ids.length * 0.005).toFixed(2)
    if (!await ask(`Price ${ids.length} selected lots?\n\nEach runs an AI pass and a comp `
                        + `lookup — roughly $${cost} of API usage, in the order shown.`)) return
    setQueuing(true)
    try { await enrichBatch(ids); onRefresh?.() } catch (e) { alertOnce(e.message) }
    finally { setQueuing(false) }
  }

  // Only the selected lots with no value yet. A lot that already has a price
  // is left alone: "Select all" on Priced Inventory once sent 1,485 priced
  // lots back through comps.
  const selectedUnpriced = selectedInView
    .filter((l) => l.enrichment?.est_resale == null && l.enrichment?.status !== 'queued')

  async function handleBulkComps() {
    const ids = selectedUnpriced.map((l) => l.lot_id)
    if (!ids.length) { notify('Every selected lot already has a value.'); return }
    const skipped = selectedInView.length - ids.length
    if (!await ask(`Look up sold comps for the ${ids.length} selected lots with no value yet, `
                        + `using their titles as-is?\n\nNo AI cost. At most ${ids.length} SoldComps `
                        + `requests against your plan - identical titles share one.`
                        + (skipped ? `\n\n${skipped} selected lots already have a value and are left alone.` : ''))) return
    setQueuing(true)
    try {
      const r = await repriceSelected(ids)
      if (r.already_running) notify('A re-price is already running; let it finish first.')
      onRefresh?.()
    } catch (e) { alertOnce(e.message) }
    finally { setQueuing(false) }
  }

  // Both of these are per-auction underneath: HiBid serves a whole catalogue
  // at a time, and a sale's shipping terms are one page for all its lots. So
  // ticking one lot of a 300-lot sale moves all 300 - which the dialog says
  // outright, from the server's own count, rather than letting the user find
  // out afterwards.
  async function handleSelectionJob(kind) {
    const ids = selectedInView.map((l) => l.lot_id)
    if (!ids.length) return
    const call = kind === 'bids' ? refreshBidsForLots : analyzeShippingForLots
    setQueuing(true)
    try {
      const plan = await call(ids, { dryRun: true })
      if (!plan.auctions) {
        notify('None of the selected lots are in an open HiBid sale.\n\n'
              + 'A sale that has already closed is left alone.')
        return
      }
      const sales = `${plan.auctions} sale${plan.auctions === 1 ? '' : 's'}`
      const what = kind === 'bids'
        ? `Re-pull current bids for your ${plan.selected} selected `
          + `lot${plan.selected === 1 ? '' : 's'}?\n\n`
          + `Reads ${plan.covered === 1 ? 'that lot' : `those ${plan.covered} lots`} directly `
          + `from HiBid, one small request each - not the `
          + `${plan.catalogue_lots.toLocaleString()}-lot `
          + `catalogue${plan.auctions === 1 ? '' : 's'} behind `
          + `${plan.covered === 1 ? 'it' : 'them'}. Free - no AI.\n\n`
          + `HiBid has no by-id lookup, so this finds each lot by its number. If one `
          + `cannot be found that way its whole sale is read instead `
          + `(${plan.fetch_pages.toLocaleString()} `
          + `page${plan.fetch_pages === 1 ? '' : 's'}), rather than guessing it has closed.`
        : `Read the shipping terms of the ${sales} behind your ${plan.selected} selected `
          + `lot${plan.selected === 1 ? '' : 's'}?\n\n`
          + `One AI call per sale, roughly $${(plan.auctions * 0.01).toFixed(2)}. Whatever it `
          + `finds is then spent: every priced lot of `
          + `${plan.auctions === 1 ? 'that sale' : 'those sales'} is re-costed with the real `
          + `shipping instead of the $15 default - up to `
          + `${plan.lots_affected.toLocaleString()} lots.`
      const notes = [
        plan.skipped_not_hibid
          ? `${plan.skipped_not_hibid} selected lot${plan.skipped_not_hibid === 1 ? '' : 's'} `
            + `skipped - no HiBid sale behind ${plan.skipped_not_hibid === 1 ? 'it' : 'them'}.`
          : '',
        plan.skipped_closed
          ? `${plan.skipped_closed} skipped - already closed.` : '',
        plan.already_queued
          ? `One of these is already queued; this adds another.` : '',
        plan.auction_names.length
          ? `Sales: ${plan.auction_names.join(', ')}` : '',
      ].filter(Boolean)
      if (!await ask([what, ...notes].join('\n\n'))) return
      await call(ids)
      onRefresh?.()
    } catch (e) { alertOnce(e.message) }
    finally { setQueuing(false) }
  }

  const handleBulkHide = async (hidden) => {
    const ids = selectedInView.map((l) => l.lot_id)
    if (hidden && ids.length > 1
        && !(await ask(`Hide ${ids.length.toLocaleString()} selected lots?\n\nThey drop out of every view. "Show hidden" in Filters brings them back.`, { confirmLabel: `Hide ${ids.length.toLocaleString()} lots` }))) return
    await bulkEach(ids, (id) => setHidden(id, hidden))
    if (hidden && ids.length > 1) notify(`Hid ${ids.length.toLocaleString()} lots.`)
  }
  const handleBulkWatch = (watched) =>
    bulkEach(selectedInView.map((l) => l.lot_id), (id) => setWatch(id, watched))

  const smallBtn = { fontSize: 13, padding: '4px 9px' }
  // A button reading "AI check 0 worth" is a control that cannot do
  // anything, sitting where the eye goes. Nothing left to check at all: no
  // control. Something left, but not at this threshold: the input stays, so
  // the threshold can be lowered to reach it, and the dead button goes.
  const aiCheck = aiReachable.length > 0 && (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: 13 }}>
      {aiTargets.length > 0 ? (
        <button onClick={handleEnrichMatching} disabled={queuing}
                title="AI reads the photo and listing for condition and a closer identification, then prices again. Only lots already priced at or above the amount, most valuable first (asks first, shows cost)">
          AI check {aiTargets.length.toLocaleString()} worth
        </button>
      ) : (
        <span style={{ color: 'var(--muted)' }}>Nothing to AI check at</span>
      )}
      $<input type="number" min="0" value={aiMin} aria-label="Lowest resale worth an AI check, in dollars"
              onChange={(ev) => setAiMin(ev.target.value)}
              title="Only lots priced at or above this"
              style={{ width: 56 }} />+
    </span>
  )
  const likeBar = likeOffer && (
    <div role="status"
         style={{ display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'center',
                  margin: '6px 0', padding: '6px 8px', borderRadius: 6,
                  border: '1px solid var(--border, #555)' }}>
      <span>
        <strong>{offerLabel(likeOffer)}</strong>
        {likeOffer.titles[0] && (
          <span style={{ color: 'var(--muted)' }}> — {likeOffer.titles[0].slice(0, 60)}
            {likeOffer.count > 1 ? ' and others' : ''}</span>
        )}
      </span>
      <button className="primary" style={smallBtn} onClick={acceptLikeOffer}
              data-track="Hide the rest like this"
              title={`Hide every lot matching "${likeOffer.key}" — the same product, listed once per asset tag`}>
        Hide {likeOffer.count.toLocaleString()} more
      </button>
      <button style={smallBtn} onClick={dismissLikeOffer}
              data-track="Keep the rest like this">
        Keep them
      </button>
    </div>
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
      <button className="primary" style={smallBtn} onClick={handleBulkComps}
              disabled={queuing || !selectedUnpriced.length}
              data-track="Comps only (selected lots)"
              title="Sold-comps lookup on the selected lots that have no value yet, on their titles as-is. Lots already priced are left alone. No AI cost (asks first, shows the request count)">
        {selectedUnpriced.length
          ? `Price ${selectedUnpriced.length.toLocaleString()} with comps (no AI)`
          : 'All selected are priced'}
      </button>
      <button style={smallBtn} onClick={handleBulkPrice} disabled={queuing}
              title="AI reads each selected lot's photo and listing for condition and a closer identification, then prices it (asks first, shows cost)">
        AI check selected
      </button>
      <button style={smallBtn} onClick={() => handleSelectionJob('bids')}
              disabled={queuing}
              data-track="Refresh bids (selected lots)"
              title="Re-pull the current bid from HiBid for the sales behind the selected lots, and re-grade each lot at its new bid. Free - no AI (asks first, and says how many lots that really moves)">
        Refresh bids
      </button>
      <button style={smallBtn} onClick={() => handleSelectionJob('shipping')}
              disabled={queuing}
              data-track="Calculate shipping (selected lots)"
              title="Read the shipping terms of the sales behind the selected lots and re-cost their lots with the real figure instead of the $15 default (asks first, shows cost)">
        Calculate shipping
      </button>
      <button style={smallBtn} onClick={() => handleBulkHide(true)}>Hide</button>
      <button style={smallBtn} onClick={() => handleBulkHide(false)}>Unhide</button>
      <button style={smallBtn} onClick={() => handleBulkWatch(true)}>Watch</button>
      <button style={smallBtn} onClick={() => handleBulkWatch(false)}>Unwatch</button>
      <button style={smallBtn} onClick={() => setSelected(new Set())}>Clear selection</button>
    </div>
  )

  if (!lots.length) {
    return (
      <>
        {(toolbar || toolbarEnd) && <div className="inv-toolbar">{toolbar}<span className="inv-spacer" />{toolbarEnd}</div>}
        {panel}
        <p>Nothing to show. The filters above may be hiding everything; loosen one, or scan auctions and import one.</p>
      </>
    )
  }

  // What's on screen vs. what the filters matched, and the pager.
  const pg = pageWindow(sorted.length, page, pageSize)
  const pageRows = sorted.slice(pg.start, pg.end)
  const countLine = (
    <span style={{ fontSize: 13, color: 'var(--muted)' }}>
      {arrange === 'auction'
        ? `${groups.length.toLocaleString()} ${groups.length === 1 ? 'auction' : 'auctions'}, ${sorted.length.toLocaleString()} lots`
        : showingText(sorted.length, pg.start, pg.end)}
      {sorted.length !== lots.length && ` (filtered from ${lots.length.toLocaleString()})`}
    </span>
  )
  const go = (n) => { setPage(n); window.scrollTo?.({ top: 0 }) }
  const pager = pg.pages > 1 && (
    <nav className="pager" aria-label="Pages">
      <button onClick={() => go(1)} disabled={pg.page === 1} title="First page" aria-label="First page"
              data-track="First page">«</button>
      <button onClick={() => go(pg.page - 1)} disabled={pg.page === 1} title="Previous page" aria-label="Previous page"
              data-track="Previous page">‹</button>
      {pageButtons(pg.page, pg.pages).map((b, i) => (b === '…'
        ? <span key={`gap-${i}`} className="gap">…</span>
        : <button key={b} onClick={() => go(b)} className={b === pg.page ? 'current' : undefined}
                  aria-current={b === pg.page ? 'page' : undefined}
                  data-track="Page number">{b}</button>))}
      <button onClick={() => go(pg.page + 1)} disabled={pg.page === pg.pages} title="Next page" aria-label="Next page"
              data-track="Next page">›</button>
      <button onClick={() => go(pg.pages)} disabled={pg.page === pg.pages} title="Last page" aria-label="Last page"
              data-track="Last page">»</button>
    </nav>
  )
  const lengthMenu = (
    <label style={{ fontSize: 13, color: 'var(--muted)', whiteSpace: 'nowrap' }}>
      Show{' '}
      <select value={pageSize} onChange={(ev) => setPageSize(Number(ev.target.value))}
              style={{ padding: '2px 4px' }}>
        {PAGE_SIZES.map((n) => <option key={n} value={n}>{n}</option>)}
      </select>
      {' '}per page
    </label>
  )

  // One lot's phone card; `kind` as for renderRow.
  const renderCard = (lot, kind) => {
          const e = lot.enrichment || {}
          const gold = e.roi_status === 'GOLD MINE'
          const overbid = kind !== 'addon' && isOverbid(lot, e)
          return (
            <div key={lot.lot_id}
                 className={`card${gold ? ' row-gold' : kind === 'addon' ? ' row-addon' : overbid ? ' row-overbid' : ''}`}
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
                  data-track={lot.watched ? 'Stop watching lot' : 'Watch lot'}
                  aria-label="Watch lot" aria-pressed={!!lot.watched}
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
                <button
                  className="bare"
                  onClick={() => handleHideLike(lot.lot_id, !lot.hidden)}
                  data-track={lot.hidden ? 'Show all like this' : 'Hide all like this'}
                  title={lot.hidden
                    ? 'Bring back every lot that is the same product as this one'
                    : 'Hide every lot that is the same product as this one — asks first'}
                  style={{ fontSize: 12, padding: '0 6px 0 0', opacity: 0.55 }}>
                  {lot.hidden ? '+ all' : '+ all'}
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
                <button type="button" className="link-like"
                        onClick={() => onSelectAuction?.(lot.auction_id)}
                        data-track="Auction name (show its items)"
                        title="Show only this auction's items">
                  {lot.auction_name}
                </button>
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
                  <span style={{ color: 'var(--danger)', fontSize: 12 }}> rejected by audit</span>
                )}
                {houseEstimate(lot) && (
                  <span style={{ color: 'var(--muted)' }}
                        title={houseRatioTitle(lot.house_ratio, lot.house_ratio_n) || undefined}>
                    house {houseEstimate(lot)}
                    {houseRatioLabel(lot.house_ratio, lot.house_ratio_n)
                      ? ` · ${houseRatioLabel(lot.house_ratio, lot.house_ratio_n)}` : ''}
                  </span>
                )}
                <span>Max bid {gold
                  ? <span className="price-sticker">{money(e.max_bid)}</span>
                  : money(e.max_bid)}</span>
                {e.est_roi != null && (
                  <span style={{ fontWeight: 600,
                                 color: Number(e.est_roi) < 0 ? 'var(--danger)'
                                   : e.roi_status === 'GOLD MINE' ? 'var(--success)' : undefined }}>
                    ROI {Math.round(Number(e.est_roi) * 100)}%
                  </span>
                )}
              </div>
              {e.est_resale != null && <CompsPeek lot={lot} e={e} onLotUpdated={onLotUpdated} />}
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
                {kind === 'addon' && addonBadge}
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
                {rowButton(lot, { flex: 1, padding: 8 })}
              </div>
            </div>
          )
  }

  // --- the "By auction" view --------------------------------------------

  const addonBadge = (
    <span className="badge addon"
          title={`Worth adding: you're already going to this auction for a gold mine, and this lot clears your ${addonFloor}% add-on bar`}>
      Add-on
    </span>
  )

  // Where drive times start. On a phone it lives behind Filters: it is set
  // once, and beside the view switch it pushed the lots a row further down.
  const driveControl = arrange === 'auction' && onSaveDriveFrom && (
    <DriveFrom driveFrom={driveFrom} available={driveAvailable}
               onSave={onSaveDriveFrom} onClear={onClearDriveFrom} />
  )

  const arrangeControl = (
    <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 12 }}>
      {!liveView && (
      <div className="segmented" role="group" aria-label="Arrange lots">
        <button type="button" aria-pressed={arrange === 'auction'}
                className={arrange === 'auction' ? 'on' : undefined}
                onClick={() => setArrange('auction')}>By auction</button>
        <button type="button" aria-pressed={arrange === 'all'}
                className={arrange === 'all' ? 'on' : undefined}
                onClick={() => setArrange('all')}>All lots</button>
      </div>
      )}
      {arrange === 'auction' && groups.length > 1 && (
        <span className="group-all">
          <button type="button" className="link-like" data-track="Open all auctions"
                  onClick={() => setAllGroups(true)}>Open all</button>
          <span aria-hidden="true">·</span>
          <button type="button" className="link-like" data-track="Close all auctions"
                  onClick={() => setAllGroups(false)}>Close all</button>
        </span>
      )}
      {!isMobile && driveControl}
    </div>
  )

  const OTHERS_STEP = 50
  const openMore = (key, total) => setOpenGroups((prev) => {
    const next = new Map(prev)
    next.set(key, Math.min(total, (prev.get(key) || 0) + OTHERS_STEP))
    return next
  })
  const foldGroup = (key) => setOpenGroups((prev) => {
    const next = new Map(prev)
    next.set(key, 0)
    return next
  })
  const groupKey = (g) => g.auctionId ?? 'none'
  // Open by default when it has a gold mine - or always, in the Live view.
  const isGroupOpen = (g) => (liveView || g.hasGold) !== flippedGroups.has(groupKey(g))
  // Open or close every auction at once. The usage log showed auctions being
  // closed one after another, up to five in a row, to clear the way to one.
  function setAllGroups(open) {
    setFlippedGroups(new Set(groups
      .filter((g) => (liveView || g.hasGold) !== open)
      .map(groupKey)))
    if (open) {
      // An auction with nothing featured opens onto its first lots, as a
      // single open does - not onto an empty space.
      setOpenGroups((prev) => {
        const next = new Map(prev)
        for (const g of groups) {
          if (!g.hasGold && !next.get(groupKey(g))) {
            next.set(groupKey(g), Math.min(g.lots.length, OTHERS_STEP))
          }
        }
        return next
      })
    }
  }

  function toggleGroup(g) {
    const key = groupKey(g)
    const opening = !isGroupOpen(g)
    setFlippedGroups((prev) => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
    // An auction with no gold mine has nothing featured: opening it shows
    // its first lots, or it would open onto an empty space.
    if (opening && !g.hasGold && !openGroups.get(key)) openMore(key, g.lots.length)
  }

  // Live Auction mode, per auction: the switch, and while on, how fresh the
  // bids are and how many watched lots have gone past your max.
  function liveControl(g) {
    const a = g.auction
    if (!a || !a.hibid_id || !onSetLive) return null
    const open = g.lots.some((l) => !(l.item_closed ?? l.auction_closed))
    if (!open && !a.live) return null
    const ago = a.live_refreshed_at
      ? Math.max(0, Math.round((now - parseUtc(a.live_refreshed_at).getTime()) / 1000)) : null
    const pastMax = a.live ? g.lots.filter((l) => l.watched && l.enrichment?.max_bid != null
      && Number(l.current_bid) > Number(l.enrichment.max_bid)).length : 0
    return (
      <span className="live-control">
        <button type="button" role="switch" aria-checked={!!a.live}
                className={`live-switch${a.live ? ' on' : ''}`}
                data-track={a.live ? 'Live auction off' : 'Live auction on'}
                title={a.live
                  ? `${a.live_auto
                    ? 'Live by itself: lots here close within the hour. Bids refresh every minute. Click to stop - it stays off.'
                    : 'Live: bids refresh every minute. Click to stop.'} ${ago == null ? 'First refresh within a minute.'
                    : ago < 90 ? `Updated ${ago}s ago.` : `Updated ${Math.round(ago / 60)} min ago.`}`
                  : 'Switch on to refresh this auction\'s bids every minute while it runs, and get an alert when a watched lot passes your max bid. Auctions go live by themselves an hour before lots close.'}
                onClick={() => onSetLive(a.id, !a.live)}>
          <span className="live-track" aria-hidden="true"><span className="live-knob" /></span>
          {a.live ? (a.live_auto ? 'Live · auto' : 'Live') : 'Live off'}
        </button>
        {pastMax > 0 && (
          <span className="live-past-max">{pastMax} watched past your max</span>
        )}
      </span>
    )
  }

  function groupHeader(g) {
    const a = g.auction
    const meta = [
      a ? [a.city, a.state].filter(Boolean).join(', ') : '',
      a?.source === 'Local Pickup' ? 'local pickup' : a?.source === 'Ship' ? 'ships' : '',
      a?.buyer_premium_mult ? `${Math.round((a.buyer_premium_mult - 1) * 100)}% buyer's premium` : '',
    ].filter(Boolean).join(' · ')
    // Where every lot of this auction is: on screen, folded below, or kept
    // out by the filters. Only showing the first was confusing - five lots
    // on screen and no word about the other two hundred.
    const key = groupKey(g)
    const open = isGroupOpen(g)
    const closedCount = open ? 0 : g.lots.length
    const filteredOut = Math.max(0, (loadedByAuction[g.auctionId] ?? g.lots.length) - g.lots.length)
    const unwatched = g.featured.filter((l) => !l.watched)
    const missing = a && a.lot_count != null && a.lots_imported != null
      && a.lots_imported < a.lot_count
      && !(a.closing_date && parseUtc(a.closing_date) < new Date())
      ? a.lot_count - a.lots_imported : 0
    return (
      // Clicking anywhere on the header that isn't one of its own controls
      // opens or closes the auction; the title button is the keyboard way.
      <div className={`group-head-inner${open ? '' : ' closed'}`}
           onClick={(ev) => {
             if (ev.target.closest('button, a, input, select, summary, details, label')) return
             // Not a button, so the page-wide tracker doesn't see it.
             track('button', { name: open ? 'Close auction group' : 'Open auction group' })
             toggleGroup(g)
           }}>
        <div className="group-title">
          <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'baseline', gap: '2px 12px' }}>
            <button type="button" className="group-toggle" aria-expanded={open}
                    data-track={open ? 'Close auction group' : 'Open auction group'}
                    onClick={() => toggleGroup(g)}
                    title={open ? 'Close this auction' : 'Open this auction'}>
              <span className="group-caret" aria-hidden="true">{open ? '▾' : '▸'}</span>
              <strong>{g.name}</strong>
            </button>
            {g.auction?.drive_minutes != null && (
              <span className="group-drive"
                    title={`One way from ${driveFrom?.label || driveFrom?.address || 'your address'}, typical traffic`}>
                <CarIcon /> {formatDrive(g.auction.drive_minutes)}
              </span>
            )}
            {liveControl(g)}
            {auctionUnpriced(g).length > 0 && (
              <button type="button" className="group-import group-act" data-track="Price this auction (comps)"
                      disabled={queuing}
                      title={`Look up sold comps for every unpriced lot here (${auctionUnpriced(g).length}), soonest-closing first. No AI cost; asks first.`}
                      onClick={() => handlePriceAuction(g)}>
                Price {auctionUnpriced(g).length.toLocaleString()} unpriced
              </button>
            )}
            {auctionAiTargets(g).length > 0 && (
              <button type="button" className="group-import group-act" data-track="AI-inspect this auction"
                      disabled={queuing}
                      title={`Further inspect with AI every priced lot here that hasn't had one (${auctionAiTargets(g).length}), most valuable first. Asks first and shows the cost.`}
                      onClick={() => handleEnrichAuction(g)}>
                AI-inspect {auctionAiTargets(g).length.toLocaleString()}
              </button>
            )}
            {missing > 0 && onImportMissing && (
              <button type="button" className="group-import" data-track="Import N missing"
                      title={`HiBid lists ${a.lot_count} lots for this sale; ${a.lots_imported} are imported. Import the rest (free, no AI calls).`}
                      onClick={() => onImportMissing(g.auctionId)}>
                Import {missing.toLocaleString()} missing
              </button>
            )}
          </div>
          <span className="group-meta">
            {[meta,
              closedCount > 0 ? `${closedCount.toLocaleString()} ${closedCount === 1 ? 'lot' : 'lots'}` : '',
            ].filter(Boolean).join(' · ')}
            {filteredOut > 0 && (
              <>{meta || closedCount > 0 ? ' · ' : ''}
                {onShowFilters ? (
                  <button type="button" className="link-like group-filtered"
                          data-track="Filtered-out count (open filters)"
                          title="Hidden by your filters: the hide rules, Gold mines only, the search or a column filter. Click to open Filters."
                          onClick={onShowFilters}>
                    {filteredOut.toLocaleString()} filtered out
                  </button>
                ) : `${filteredOut.toLocaleString()} filtered out`}
              </>
            )}
            {g.auctionId != null && onSelectAuction && (
              <>{' · '}
                <button type="button" className="link-like group-filtered"
                        data-track="Auction group name (show its items)"
                        title="Narrow the inventory to this auction alone"
                        onClick={() => onSelectAuction(g.auctionId)}>
                  show only this auction
                </button>
              </>
            )}
          </span>
        </div>
        {g.hasGold ? (
          <div className="group-basket">
            <div>
              <strong>{basketLabel(g.goldCount, g.addonCount)}</strong>
              <div className="group-meta">
                {money(g.basketBids)} in current bids · about {money(g.basketResale)} resale
              </div>
            </div>
            {unwatched.length > 0 && (
              <button type="button" data-track="Watch the gold mines and add-ons"
                      title="Get a phone alert before each of these closes"
                      onClick={() => bulkEach(unwatched.map((l) => l.lot_id), (id) => setWatch(id, true))}>
                {unwatched.length === 1 ? 'Watch it' : `Watch these ${unwatched.length}`}
              </button>
            )}
          </div>
        ) : (
          <span className="group-meta">
            No gold mines{g.bestRoi != null ? ` · best ROI ${Math.round(g.bestRoi * 100)}%` : ''}
          </span>
        )}
      </div>
    )
  }

  // Each auction: its header, its gold mines and add-ons, then the rest
  // folded away - at an auction with no gold mine, all of it.
  const renderGroups = (renderOne, asTable, span = COLUMNS.length + 2) => groups.map((g) => {
    const key = groupKey(g)
    const open = isGroupOpen(g)
    const rest = g.hasGold ? g.others : g.lots
    // An auction remembered open from last time, with nothing featured,
    // opens onto its first lots - as a click to open it does.
    const shown = Math.min(openGroups.get(key) ?? (open && !g.hasGold ? OTHERS_STEP : 0), rest.length)
    // The Live view lists every lot, in the sort order, gold and add-ons
    // highlighted where they fall rather than pulled to the top.
    const rows = !open ? []
      : liveView ? g.lots.map((l) => renderOne(l, g.kinds.get(l.lot_id)))
      : [...g.featured, ...rest.slice(0, shown)].map((l) => renderOne(l, g.kinds.get(l.lot_id)))
    const more = open && !liveView && rest.length > 0 && (
      <div className="group-more">
        {shown < rest.length && (
          <button type="button" className="link-like" data-track="Show more lots in this auction"
                  onClick={() => openMore(key, rest.length)}>
            {shown === 0
              ? (g.hasGold ? `Show ${rest.length.toLocaleString()} other lots here`
                           : `Show ${rest.length.toLocaleString()} lots`)
              : `Show ${Math.min(OTHERS_STEP, rest.length - shown)} more`}
          </button>
        )}
        {shown > 0 && (
          <button type="button" className="link-like" data-track="Fold this auction's other lots"
                  onClick={() => foldGroup(key)}>
            Fold them away
          </button>
        )}
      </div>
    )
    if (asTable) {
      return (
        <Fragment key={key}>
          <tr className="group-head"><td colSpan={span}>{groupHeader(g)}</td></tr>
          {rows}
          {more && <tr className="group-foot"><td colSpan={span}>{more}</td></tr>}
        </Fragment>
      )
    }
    return (
      <section key={key} className="group-cards" aria-label={g.name}>
        {groupHeader(g)}
        {rows}
        {more}
      </section>
    )
  })

  // --- the "By auction" rows: decision first -----------------------------
  // Lot, when it closes, where the bid stands against your ceiling, what it
  // resells for, the return, the verdict - left to right, the order the
  // decision is made in. The full column set (and a filter under each)
  // stays in "All lots".

  const aiHeaderAction = (
    <>
            {aiTargets.length > 1 && (
              <button
                disabled={queuing}
                onClick={handleEnrichMatching}
                data-track="AI check worth (header)"
                title={`AI-check the ${aiTargets.length} lots in view worth $${aiMin}+ that `
                       + `have not had one, most valuable first. Same action as the `
                       + `button above the table; it asks first and shows the cost.`}
                style={{ fontSize: 12, padding: '3px 9px' }}>
                {queuing ? <><span className="spinner" />Queuing…</>
                         : `AI check ${aiTargets.length.toLocaleString()}`}
              </button>
            )}
    </>
  )

  const closeMenu = (ev) => {
    const d = ev.currentTarget.closest('details')
    if (d) d.open = false
  }

  const starButton = (lot) => (
    <button type="button" className="bare icon-btn"
            aria-label={lot.watched ? 'Stop watching lot' : 'Watch lot'}
            title={lot.watched ? 'Watching - phone alert when this closes within 2 hours (click to stop)'
                               : 'Watch: phone alert when this closes within 2 hours'}
            data-track={lot.watched ? 'Stop watching lot' : 'Watch lot'}
            onClick={() => handleWatch(lot.lot_id, !lot.watched)}>
      <svg width="18" height="18" viewBox="0 0 24 24" fill={lot.watched ? 'currentColor' : 'none'}
           stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round" aria-hidden="true">
        <path d="M12 3.5l2.6 5.3 5.9.9-4.3 4.1 1 5.8L12 16.9l-5.2 2.7 1-5.8-4.3-4.1 5.9-.9z" />
      </svg>
    </button>
  )

  // The row's price / AI button, but only when pressing it does something.
  // A lot the AI already checked shows a disabled "AI checked" - true, and
  // noise in every row; its way past the lock lives in the row menu.
  const actionableButton = (lot, style) => {
    const a = rowAction(lot.enrichment || {}, pollingIds.has(lot.lot_id))
    return (a.step === 'comps' || a.step === 'ai') && !a.disabled ? rowButton(lot, style) : null
  }

  const rowMenu = (lot) => (
    <details className="row-menu">
      <summary aria-label="More for this lot" title="Hide, hide similar, open the listing">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
          <circle cx="5" cy="12" r="1.8" /><circle cx="12" cy="12" r="1.8" /><circle cx="19" cy="12" r="1.8" />
        </svg>
      </summary>
      <div className="row-menu-panel">
        <button type="button" data-track={lot.hidden ? 'show' : 'hide'}
                onClick={(ev) => { closeMenu(ev); handleHide(lot.lot_id, !lot.hidden) }}>
          {lot.hidden ? 'Show this lot again' : 'Hide this lot'}
        </button>
        <button type="button" data-track={lot.hidden ? 'Show all like this' : 'Hide all like this'}
                onClick={(ev) => { closeMenu(ev); handleHideLike(lot.lot_id, !lot.hidden) }}>
          {lot.hidden ? 'Show every lot like this' : 'Hide every lot like this'}
        </button>
        {rowAction(lot.enrichment || {}, pollingIds.has(lot.lot_id)).step === 'locked' && (
          <button type="button" data-track="Re-check with AI (row)"
                  title="Release the lock on this one lot and price it again. Spends another AI call."
                  onClick={(ev) => { closeMenu(ev); handleRecheck(lot.lot_id) }}>
            Re-check with AI
          </button>
        )}
        <a href={lot.lot_link} target="_blank" rel="noreferrer" onClick={closeMenu}>Open the listing</a>
      </div>
    </details>
  )

  const VERDICT_CHIP = {
    gold: 'Gold mine', addon: 'Add-on', over: 'Over your max', unpriced: 'Not priced', pass: 'Pass',
  }

  function verdictChip(lot, e, kind) {
    if (isWorking(lot)) {
      return <span className="chip"><span className="spinner" />{e.progress || (e.status === 'queued' ? 'in the queue' : 'working')}</span>
    }
    const confirmed = kind === 'gold' && (e.gold_check === 'confirmed' || e.gold_check === 'corrected')
    const why = e.gold_check === 'confirmed'
      ? `AI double-checked this gold${e.gold_check_note ? `: ${e.gold_check_note}` : ''}`
      : e.gold_check === 'corrected'
        ? `AI replaced the comp value with its own - ${e.gold_check_note || 'see price source'}`
        : e.gold_check === 'demoted'
          ? `AI demoted this gold - ${e.gold_check_note || 'value judged implausible'}`
          : kind === 'addon'
            ? `Worth adding: you're already going to this auction for a gold mine, and this clears your ${addonFloor}% add-on bar`
            : e.roi_reason || undefined
    return (
      <span className={`chip chip-${kind}`} title={why} style={why ? { cursor: 'help' } : undefined}>
        {VERDICT_CHIP[kind] || 'Pass'}{confirmed ? ' ✓' : ''}
      </span>
    )
  }

  // Bid against your ceiling: the two numbers side by side, and a bar for
  // how much of the ceiling the bid has used. A gold mine's ceiling is the
  // sticker; over it, the overage is what's said.
  function bidAgainstMax(lot, e, kind, { compact = false } = {}) {
    const bid = num(lot.current_bid)
    const max = num(e.max_bid)
    const over = kind === 'over' && bid != null && max != null
    const fill = bid != null && max ? Math.min(100, Math.round((bid / max) * 100)) : 0
    return (
      <div className="d-bid">
        <div className="d-bid-line">
          <span>Bid {money(lot.current_bid)}</span>
          {kind === 'gold' && max != null ? <span className="price-sticker">max {money(max)}</span>
            : over ? <span className="d-over">{money(bid - max)} over max</span>
            : max != null ? <span className="d-max">max {money(max)}</span>
            : !compact && rowButton(lot, { fontSize: 12.5, padding: '2px 10px' })}
        </div>
        {max != null && (
          <div className={`d-bar d-bar-${kind}`} aria-hidden="true">
            <span style={{ width: `${fill}%` }} />
          </div>
        )}
      </div>
    )
  }

  function resaleCell(lot, e, edited) {
    const ev = evidence(e)
    return (
      <>
        <EditableCell
          display={money(e.est_resale)}
          rawValue={e.est_resale}
          inputType="number"
          edited={edited.has('est_resale')}
          onSave={(v) => handleCorrect(lot.lot_id, 'est_resale', v)}
        />
        {ev && e.est_resale == null && (
          <div title={EVIDENCE_NOTE[ev] || ''}
               style={{ color: isPaleEvidence(ev) ? 'var(--warn)' : 'var(--muted)', fontSize: 11.5 }}>
            {EVIDENCE_LABEL[ev] || ev}
          </div>
        )}
        {e.gold_check === 'demoted' && (
          <div title={e.gold_check_note || 'The second-opinion audit judged this value implausible'}
               style={{ color: 'var(--danger)', fontSize: 11, fontWeight: 600, cursor: 'help' }}>
            rejected by audit
          </div>
        )}
        {houseEstimate(lot) && (
          <div style={{ color: 'var(--muted)', fontSize: 11 }}
               title={houseRatioTitle(lot.house_ratio, lot.house_ratio_n)
                 || "The auction house's own estimate range - promotional, but weak-evidence values are capped against it"}>
            house {houseEstimate(lot)}
          </div>
        )}
        {e.est_resale != null && (
          <CompsPeek lot={lot} e={e} onLotUpdated={onLotUpdated}
                     label={`${ev ? (EVIDENCE_LABEL[ev] || ev) : 'comps'}${e.comp_count ? ` · ${e.comp_count}` : ''}`}
                     labelStyle={{ fontSize: 11.5, color: ev && isPaleEvidence(ev) ? 'var(--warn)' : 'var(--muted)' }} />
        )}
      </>
    )
  }

  function lotNotes(e) {
    return (
      <>
        {e.fraud_note && (
          <div style={{ color: 'var(--warn)', fontSize: 12 }}
               title="Funko fraud check: how the listing's own words changed the pricing">
            fraud check: {e.fraud_note}
          </div>
        )}
        {e.identity_note && (
          <div className="d-flag"
               title={`AI added "${e.identity_note}", which the listing never says - so the value came from the listing's own title and the gold badge is withheld. Correct the title to confirm what it is.`}>
            identity unsure
          </div>
        )}
        {e.notes && e.ai_source === 'vision-itemized' && (
          <details style={{ fontSize: 12, color: 'var(--muted)' }}>
            <summary>itemized breakdown</summary>
            <pre style={{ whiteSpace: 'pre-wrap', margin: 0 }}>{e.notes}</pre>
          </details>
        )}
      </>
    )
  }

  function lotTags(lot, e) {
    return (
      <>
        {e.bolo_brand && (
          <span className="badge bolo" title={`BOLO match: ${e.bolo_brand} (tier ${e.bolo_tier ?? '?'})`}>
            BOLO · {e.bolo_brand}
          </span>
        )}
        {e.auth_required && (
          <span className="badge bolo"
                title="Luxury/precious-metal match - resale depends on authentication; don't trust the comps until verified in hand">
            verify
          </span>
        )}
        {lot.logistics_ease === 'HARD' && <span className="badge hard">Hard to ship</span>}
      </>
    )
  }

  const renderDecisionRow = (lot, kind) => {
    const e = lot.enrichment || {}
    const edited = new Set(e.user_overrides || [])
    const closing = closesIn(lot.closes_at, now)
    const roi = e.est_roi == null ? null : Math.round(Number(e.est_roi) * 100)
    const rowClass = kind === 'gold' ? 'row-gold' : kind === 'addon' ? 'row-addon'
      : kind === 'over' ? 'row-overbid' : undefined
    return (
      <tr key={lot.lot_id} className={rowClass}>
        <td>
          <input type="checkbox" checked={selected.has(lot.lot_id)}
                 onChange={() => setSelected(toggle(selected, lot.lot_id))}
                 title="Select this lot for a bulk action" />
        </td>
        <td className="d-lot">
          <div className="d-title">
            <a href={lot.lot_link} target="_blank" rel="noreferrer"
               style={lot.hidden ? { opacity: 0.5, textDecoration: 'line-through' } : undefined}>{lot.title}</a>
            {lotTags(lot, e)}
          </div>
          <div className="d-sub">
            #{lot.lot_number || '—'}
            {e.enriched_title && e.enriched_title !== lot.title && (
              <>{' · '}
                <EditableCell
                  display={e.enriched_title}
                  rawValue={e.enriched_title}
                  edited={edited.has('enriched_title')}
                  onSave={(v) => handleCorrect(lot.lot_id, 'enriched_title', v)}
                />
              </>
            )}
            {e.verdict && (
              <>{' · '}
                <EditableCell
                  display={e.verdict}
                  rawValue={e.verdict}
                  options={VERDICTS}
                  edited={edited.has('verdict')}
                  onSave={(v) => handleCorrect(lot.lot_id, 'verdict', v)}
                />
              </>
            )}
          </div>
          {lotNotes(e)}
        </td>
        <td className={closing.urgent ? 'd-urgent' : undefined} style={{ whiteSpace: 'nowrap' }}>
          {(lot.item_closed ?? lot.auction_closed) ? 'closed' : closing.text}
        </td>
        <td>{bidAgainstMax(lot, e, kind)}</td>
        <td className="num">{resaleCell(lot, e, edited)}</td>
        <td className="num" title={roiTooltip(lot, e)}
            style={{ cursor: roi != null ? 'help' : undefined }}>
          <span className={`d-roi d-roi-${kind}`}>{roi == null ? '—' : `${roi}%`}</span>
        </td>
        <td>
          {verdictChip(lot, e, kind)}
          {e.status === 'failed' && e.error_message && (
            <div style={{ color: 'var(--error)', fontSize: 12 }}>{e.error_message.slice(0, 80)}</div>
          )}
        </td>
        <td className="d-actions">
          {kind !== 'unpriced' && actionableButton(lot, { fontSize: 12, padding: '3px 8px' })}
          {starButton(lot)}
          {rowMenu(lot)}
        </td>
      </tr>
    )
  }

  // The phone's version: the verdict and the clock lead, because on a
  // phone the question is "do I bid on this, and how soon".
  const renderDecisionCard = (lot, kind) => {
    const e = lot.enrichment || {}
    const closing = closesIn(lot.closes_at, now)
    const roi = e.est_roi == null ? null : Math.round(Number(e.est_roi) * 100)
    const bid = num(lot.current_bid)
    const max = num(e.max_bid)
    const ev = evidence(e)
    const cls = kind === 'gold' ? ' row-gold' : kind === 'addon' ? ' row-addon' : kind === 'over' ? ' row-overbid' : ''
    return (
      <div key={lot.lot_id} className={`card d-card${cls}`}>
        <div className="d-card-top">
          {kind === 'gold' && max != null ? <span className="price-sticker">max {money(max)}</span>
            : kind === 'over' && bid != null && max != null
              ? <span className="d-over">{money(bid - max)} over your max</span>
              : <span>{verdictChip(lot, e, kind)}{kind === 'addon' && max != null ? <span className="d-max"> · max {money(max)}</span> : null}</span>}
          <span className={closing.urgent ? 'd-urgent' : undefined}>
            {(lot.item_closed ?? lot.auction_closed) ? 'closed' : `closes ${closing.text}`}
          </span>
        </div>
        <div style={{ display: 'flex', gap: 6, alignItems: 'baseline' }}>
          <input type="checkbox" checked={selected.has(lot.lot_id)}
                 onChange={() => setSelected(toggle(selected, lot.lot_id))}
                 title="Select this lot for a bulk action" />
          <a href={lot.lot_link} target="_blank" rel="noreferrer" className="d-card-title"
             style={lot.hidden ? { opacity: 0.5, textDecoration: 'line-through' } : undefined}>{lot.title}</a>
        </div>
        <div className="d-sub">#{lot.lot_number || '—'}{e.enriched_title && e.enriched_title !== lot.title ? ` · ${e.enriched_title}` : ''}</div>
        {lotNotes(e)}
        <div className="d-card-nums">
          <span>
            Bid {money(lot.current_bid)}
            {e.est_resale != null && <> · resale {money(e.est_resale)}</>}
            {ev && isPaleEvidence(ev) && <span style={{ color: 'var(--warn)' }}> ({EVIDENCE_LABEL[ev] || ev})</span>}
          </span>
          {roi != null && <span className={`d-roi d-roi-${kind}`}>ROI {roi}%</span>}
        </div>
        {max != null && (
          <div className={`d-bar d-bar-${kind}`} aria-hidden="true">
            <span style={{ width: `${bid != null && max ? Math.min(100, Math.round((bid / max) * 100)) : 0}%` }} />
          </div>
        )}
        <div className="d-card-tags">{lotTags(lot, e)}</div>
        {e.est_resale != null && (
          <CompsPeek lot={lot} e={e} onLotUpdated={onLotUpdated}
                     label={`${ev ? (EVIDENCE_LABEL[ev] || ev) : 'comps'}${e.comp_count ? ` · ${e.comp_count}` : ''}`}
                     labelStyle={{ fontSize: 13, color: ev && isPaleEvidence(ev) ? 'var(--warn)' : 'var(--muted)' }} />
        )}
        <div className="d-card-actions">
          {actionableButton(lot, { flex: 1, padding: 8 }) || <span style={{ flex: 1 }} />}
          {starButton(lot)}
          {rowMenu(lot)}
        </div>
      </div>
    )
  }

  const goldGroups = groups.filter((g) => g.hasGold)
  const addonTotal = groups.reduce((n, g) => n + g.addonCount, 0)
  const goldSummary = arrange === 'auction' && goldGroups.length > 0 && (
    <div className="gold-summary">
      <strong>Gold mines at {goldGroups.length} {goldGroups.length === 1 ? 'auction' : 'auctions'}.</strong>{' '}
      {addonTotal
        ? `${addonTotal} more ${addonTotal === 1 ? 'lot there is' : 'lots there are'} worth adding, since you'd be picking up anyway.`
        : 'Nothing else there clears the add-on bar.'}
      <label className="addon-bar"
             title="At an auction where you already have a gold mine, a lot with at least this ROI is worth adding - you're making the trip anyway">
        Add-on bar{' '}
        <NumberField step="10" value={addonFloor} onCommit={setAddonFloor}
                     data-track="Add-on ROI bar" aria-label="Add-on bar, % ROI"
                     style={{ width: 56 }} />
        {' '}% ROI
      </label>
    </div>
  )
  // Column filters are set in "All lots" and have no widgets here, so say
  // when one is quietly narrowing this view.
  const columnFilterNote = arrange === 'auction' && activeFilterCount > 0 && (
    <div style={{ fontSize: 13, color: 'var(--muted)', margin: '4px 0 8px' }}>
      {activeFilterCount} column {activeFilterCount === 1 ? 'filter' : 'filters'} from All lots {activeFilterCount === 1 ? 'is' : 'are'} also narrowing this view.{' '}
      <button type="button" className="link-like" style={{ color: 'var(--link)' }}
              onClick={() => setColFilters({})}>Clear {activeFilterCount === 1 ? 'it' : 'them'}</button>
    </div>
  )

  const searchBox = (
    <label className="inv-search">
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor"
           strokeWidth="2" strokeLinecap="round" aria-hidden="true">
        <circle cx="11" cy="11" r="7" /><path d="M20 20l-4-4" />
      </svg>
      <input type="search" value={search} onChange={(ev) => handleSearch(ev.target.value)}
             placeholder="Search title, auction or lot #" aria-label="Search inventory" />
    </label>
  )
  const priceButton = (unpricedShown.length > 0 || queuing) && (
    <button className="primary" onClick={handleCompsMatching}
            disabled={queuing}
            data-track="Price N with comps (no AI)"
            title="The default: sold comps for every unpriced lot matching the filters, on each lot's own title. No AI cost.">
      {queuing ? <><span className="spinner" />Queuing…</>
               : `Price ${unpricedShown.length.toLocaleString()} unpriced ${unpricedShown.length === 1 ? 'lot' : 'lots'}`}
    </button>
  )

  const DECISION_HEADS = [
    // "Lot" sorts by lot number, numerically (37 before 114, 198f beside
    // 198) - it sorted by title, which read as a broken number sort.
    { label: 'Lot', sort: 'lot_number' }, { label: 'Closes', sort: 'closes' },
    { label: 'Bid against your max', sort: 'bid' }, { label: 'Resale', sort: 'est_resale', num: true },
    { label: 'ROI', sort: 'roi', num: true }, { label: 'Verdict', sort: 'verdict' },
  ]
  const decisionTable = (
    <table className="data-table lot-table decision-table">
      <thead style={{ position: 'sticky', top: 'var(--statusbar-h, 0px)', zIndex: 10, background: 'var(--card-bg)' }}>
        <tr>
          <th style={{ width: 28 }}>
            <input type="checkbox" title="Select every lot in view"
                   checked={allSelected(selected, sorted)}
                   onChange={(ev) => setSelected(ev.target.checked ? selectAll(sorted) : new Set())} />
          </th>
          {DECISION_HEADS.map((h) => (
            <th key={h.label} className={h.num ? 'num' : undefined}
                aria-sort={sort.key === h.sort ? (sort.dir === 1 ? 'ascending' : 'descending') : undefined}>
              <button type="button" className="th-sort" onClick={() => handleSort(h.sort)} title="Sort by this"
                      data-track={`Sort by ${h.label}`}>
                {h.label}
                <span className={`sort-arrows${sort.key === h.sort ? (sort.dir === 1 ? ' asc' : ' desc') : ''}`}
                      aria-hidden="true"><span>▲</span><span>▼</span></span>
              </button>
            </th>
          ))}
          <th style={{ whiteSpace: 'nowrap', textAlign: 'right' }}>{aiHeaderAction}</th>
        </tr>
      </thead>
      <tbody>{renderGroups(renderDecisionRow, true, DECISION_HEADS.length + 2)}</tbody>
    </table>
  )

  if (isMobile) {
    return (
      <>
        {/* The phone's controls, as the design has them: search and Filters
            on one row; the view, drive-from and More actions on the next;
            the price button; everything else behind Filters. */}
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginBottom: 10 }}>
          <input
            type="search"
            value={search}
            onChange={(ev) => handleSearch(ev.target.value)}
            placeholder="Search lots"
            aria-label="Search inventory"
            style={{ flex: '1 1 0', minWidth: 0, padding: 8, fontSize: 16 }}
          />
          {phoneFilters ?? toolbar}
          <div style={{ flexBasis: '100%', display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'center' }}>
            {arrangeControl}
            {toolbarEnd}
          </div>
          {filtersOpen && phoneBehindFilters && (
            <div style={{ flexBasis: '100%', display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'center' }}>
              {driveControl}
              {phoneBehindFilters}
              {arrange !== 'auction' && toolbarAllLots}
            </div>
          )}
          {panel && <div style={{ flexBasis: '100%' }}>{panel}</div>}
          {(arrange !== 'auction' || filtersOpen) && (
          <select
            aria-label="Sort lots"
            value={MOBILE_SORTS.findIndex((s) => s.key === sort.key && s.dir === sort.dir)}
            onChange={(ev) => {
              const s = MOBILE_SORTS[Number(ev.target.value)] ?? MOBILE_SORTS[0]
              setSort({ key: s.key, dir: s.dir })
            }}
            style={{ flex: 1, padding: 6, fontSize: 14, maxWidth: '48%' }}
          >
            {MOBILE_SORTS.map((s, i) => <option key={s.label} value={i}>{s.label}</option>)}
          </select>
          )}
          {/* Every filterable column, generated from the same list the
              desktop header uses - the phone used to hand-maintain a subset
              with no bid, cost, resale, max-bid, ROI or auction filter. The
              title search above already binds the one text column. */}
          {arrange !== 'auction' && (
          <button style={{ flex: '1 1 100%', padding: 8, fontSize: 14 }}
                  onClick={() => setShowFilters((v) => !v)}
                  aria-expanded={showFilters}>
            {showFilters ? 'Hide column filters' : `Column filters${activeFilterCount ? ` (${activeFilterCount} on)` : ''}`}
          </button>
          )}
          {arrange !== 'auction' && showFilters && COLUMNS.filter((c) => c.filter && c.filter !== 'text').map((c) => (
            <select
              key={c.key}
              value={colFilters[c.key] ?? ''}
              aria-label={`Filter by ${c.label}`}
              onChange={(ev) => setFilter(c.key, ev.target.value)}
              style={{ flex: '1 1 47%', padding: 6, fontSize: 14, maxWidth: '48%' }}
            >
              <option value="">{c.label}: all</option>
              {presetsFor(c, distinctValues).map((o) => (
                <option key={o.value} value={o.value}>{o.label}</option>
              ))}
            </select>
          ))}
          {arrange !== 'auction' && activeFilterCount > 0 && (
            <button style={{ flex: '1 1 100%', padding: 6, fontSize: 13 }}
                    onClick={() => setColFilters({})}>
              Clear {activeFilterCount} filter{activeFilterCount === 1 ? '' : 's'}
            </button>
          )}
          {(unpricedShown.length > 0 || queuing) && (
            <button className="primary" onClick={handleCompsMatching}
                    disabled={queuing}
                    data-track="Price N with comps (no AI)"
                    title="The default: sold comps for every unpriced lot matching the filters, on each lot's own title. No AI cost."
                    style={{ flex: '1 1 100%', padding: 10, fontSize: 15 }}>
              {queuing ? <><span className="spinner" />Queuing…</>
                       : `Price ${unpricedShown.length.toLocaleString()} unpriced ${unpricedShown.length === 1 ? 'lot' : 'lots'}`}
            </button>
          )}
          {arrange !== 'auction' && aiCheck}
          {arrange !== 'auction' && !selectedInView.length && sorted.length > 0 && (
            <button style={{ flex: '1 1 100%', padding: 6, fontSize: 13 }}
                    onClick={() => setSelected(selectAll(sorted))}
                    title="Select every lot matching the current filters, then act on them together">
              Select all {sorted.length.toLocaleString()}
            </button>
          )}
          {likeBar && <div style={{ flexBasis: '100%' }}>{likeBar}</div>}
          {bulkBar && <div style={{ flexBasis: '100%' }}>{bulkBar}</div>}
          {anyQueued && <span style={{ flexBasis: '100%' }}><span className="spinner" />{lots.filter((l) => l.enrichment?.status === 'queued').length} lots in the queue… updates when they finish</span>}
          {/* Length menu up here; the count and pager sit under the cards,
              the way DataTables lays a table out. */}
          {arrange !== 'auction' && <div style={{ flexBasis: '100%' }}>{lengthMenu}</div>}
        </div>
        {goldSummary}
        {columnFilterNote}
        {arrange === 'auction' ? renderGroups(renderDecisionCard, false) : pageRows.map((lot) => renderCard(lot))}
        <div className="table-footer">{countLine}{arrange === 'auction' ? null : pager}</div>
      </>
    )
  }

  // One lot's table row. `kind` comes from the auction grouping; a flat
  // list passes none.
  const renderRow = (lot, kind) => {
          const e = lot.enrichment || {}
          const gold = e.roi_status === 'GOLD MINE'
          const ev = evidence(e)
          const paleGold = gold && isPaleEvidence(ev)
          const overbid = kind !== 'addon' && isOverbid(lot, e)
          const edited = new Set(e.user_overrides || [])
          return (
            <tr key={lot.lot_id}
                className={gold ? 'row-gold' : kind === 'addon' ? 'row-addon' : overbid ? 'row-overbid' : undefined}
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
                  data-track={lot.watched ? 'Stop watching lot' : 'Watch lot'}
                  aria-label="Watch lot" aria-pressed={!!lot.watched}
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
                <button
                  className="bare"
                  onClick={() => handleHideLike(lot.lot_id, !lot.hidden)}
                  data-track={lot.hidden ? 'Show all like this' : 'Hide all like this'}
                  title={lot.hidden
                    ? 'Bring back every lot that is the same product as this one'
                    : 'Hide every lot that is the same product as this one — asks first'}
                  style={{ fontSize: 12, padding: '0 6px 0 0', opacity: 0.55 }}>
                  + all
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
                <button type="button" className="link-like"
                        onClick={() => onSelectAuction?.(lot.auction_id)}
                        data-track="Auction name (show its items)"
                        title="Show only this auction's items">
                  {lot.auction_name}
                </button>
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
                       style={{ color: isPaleEvidence(ev) ? 'var(--warn)' : 'var(--muted)',
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
                       style={{ color: 'var(--danger)', fontSize: 11, fontWeight: 600,
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
                {e.est_resale != null && <CompsPeek lot={lot} e={e} onLotUpdated={onLotUpdated} />}
              </td>
              <td className="num" style={cell}>
                {gold ? <span className="price-sticker">{money(e.max_bid)}</span> : money(e.max_bid)}
              </td>
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
                {kind === 'addon' && <>{addonBadge}{' '}</>}
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
                {rowButton(lot)}
              </td>
            </tr>
          )
  }

  return (
    <>
    {/* One toolbar, as the design lays it out: find, arrange, scope and
        lenses on the left; the actions on the right. */}
    <div className="inv-toolbar">
      {searchBox}
      {arrangeControl}
      {toolbar}
      {arrange !== 'auction' && toolbarAllLots}
      {arrange !== 'auction' && lengthMenu}
      <span className="inv-spacer" />
      {anyQueued && <span className="inv-note"><span className="spinner" />{lots.filter((l) => l.enrichment?.status === 'queued').length} in the queue</span>}
      {arrange !== 'auction' && aiCheck}
      {toolbarEnd}
      {priceButton}
    </div>
    {panel}
    {likeBar}
    {bulkBar}
    {goldSummary}
    {/* No overflow wrapper: an overflow-x container becomes the scrollport
        position:sticky binds to, and the thead's top offset then displaces
        it INSIDE the table by the status bar's height — a blank band with
        the header floating over the first rows whenever a job is running.
        A too-narrow window falls back to page-level horizontal scrolling. */}
    {columnFilterNote}
    {arrange === 'auction' ? decisionTable : (<>
    {sort.key && (
      <style>{`.lot-table tbody td:nth-child(${COLUMNS.findIndex((c) => c.key === sort.key) + 2}) { background-image: linear-gradient(var(--sorted-tint), var(--sorted-tint)); }`}</style>
    )}
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
              aria-sort={sort.key === c.key ? (sort.dir === 1 ? 'ascending' : 'descending') : undefined}
              style={c.key === 'title' ? { width: '28%', minWidth: 220 } : undefined}
            >
              <button type="button" className="th-sort" onClick={() => handleSort(c.key)}
                      title="Sort by this" data-track={`Sort by ${c.label}`}>
              {c.label}
              <span className={`sort-arrows${sort.key === c.key ? (sort.dir === 1 ? ' asc' : ' desc') : ''}`}
                    aria-hidden="true">
                <span>▲</span><span>▼</span>
              </span>
              </button>
            </th>
          ))}
          {/* The set-level AI action, in the column its per-row twin lives
              in. The usage log is unambiguous: 34 one-at-a-time presses of
              the row button against 11 of every bulk path combined, in runs
              of up to nine - and each row press spends money. The toolbar
              already had this button and it was used once. Moving the saved-
              auction equivalent next to its rows took that one from 0 to used
              and stopped the per-row clicking, so this is the same move. */}
          <th style={{ whiteSpace: 'nowrap' }}>
            {aiHeaderAction}
          </th>
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
                  placeholder="Search…"
                  aria-label={`Filter by ${c.label}`}
                  autoComplete="off" spellCheck={false}
                  style={{ width: '90%', minWidth: 60 }}
                />
              ) : (
                <select
                  value={colFilters[c.key] ?? ''}
                  onChange={(ev) => setFilter(c.key, ev.target.value)}
                  aria-label={`Filter by ${c.label}`}
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
        {pageRows.map((lot) => renderRow(lot))}
      </tbody>
    </table>
    </>)}
    <div className="table-footer">{countLine}{arrange === 'auction' ? null : pager}</div>
    </>
  )
}
