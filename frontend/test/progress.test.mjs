// Run with a Node that can read ESM (any modern one):
//   node frontend/test/progress.test.mjs
//
// No test framework here on purpose — this is one module of arithmetic,
// and the bug it covers (the import ETA vanishing at the fetch/save phase
// flip) is exactly the kind that renders as 'nothing' and goes unnoticed.

import { sampleProgress, etaText } from '../src/lib/progress.js'

let failures = 0
const check = (name, cond, extra = '') => {
  console.log(`${cond ? '  ok  ' : '  FAIL'} ${name}${extra ? ' — ' + extra : ''}`)
  if (!cond) failures++
}

// --- an import, as it actually behaves -------------------------------------
// Phase 1: fetch, 100 lots per page, ~1s per page, total = HiBid's count.
// Phase 2: save, current RESETS to 0 and total changes to the open-lot count.
const map = new Map()
let t = 0
const job = { id: 'imp', total: 2400, current: 0 }

for (let fetched = 100; fetched <= 1200; fetched += 100) {
  t += 1000
  sampleProgress(map, [{ ...job, current: fetched }], t)
}
const midFetch = etaText(map, { ...job, current: 1200 })
check('ETA shown during fetch', midFetch !== '', `"${midFetch.trim()}"`)

// the phase flip: counter resets, total changes
t += 1000
sampleProgress(map, [{ id: 'imp', total: 2169, current: 0 }], t)
check('series restarted at the phase flip', map.get('imp').length === 1,
      `${map.get('imp').length} samples`)

for (let saved = 10; saved <= 200; saved += 10) {
  t += 1000
  sampleProgress(map, [{ id: 'imp', total: 2169, current: saved }], t)
}
const midSave = etaText(map, { id: 'imp', total: 2169, current: 200 })
check('ETA shown during save (the bug)', midSave !== '', `"${midSave.trim()}"`)
check('save ETA is plausible', /~3[0-9] min left|~3 min left|~19[0-9]s|~1[0-9] min/.test(midSave)
      || midSave.includes('min left'), `"${midSave.trim()}"`)

// --- what the OLD code did, for contrast -----------------------------------
// Faithful replay: one series, no reset, 20-sample cap. The bug is not that
// the ETA is always missing — it vanishes right after the flip, then comes
// back WRONG once enough save samples accumulate to make `done` positive
// again while the rate still spans two unrelated phases.
const naive = new Map()
let t2 = 0
const push = (current, total) => {
  const arr = naive.get('imp') ?? []
  arr.push({ t: t2, current, total })
  while (arr.length > 20) arr.shift()
  naive.set('imp', arr)
}
for (let fetched = 100; fetched <= 1200; fetched += 100) { t2 += 1000; push(fetched, 2400) }
t2 += 1000; push(0, 2169)
for (let saved = 10; saved <= 50; saved += 10) { t2 += 1000; push(saved, 2169) }
check('old behaviour: ETA gone just after the flip',
      etaText(naive, { id: 'imp', total: 2169, current: 50 }) === '')

// How long is the blackout? The 20-sample cap eventually flushes the fetch
// samples out on its own, so the ETA recovers without help — the bug is a
// gap, not a permanent loss. Status polls once a second, so each sample is
// about a second of the user staring at a bar with no estimate on it.
let blackout = 0
for (let saved = 60; saved <= 600; saved += 10) {
  t2 += 1000
  push(saved, 2169)
  if (etaText(naive, { id: 'imp', total: 2169, current: saved }) === '') blackout++
  else break
}
check('old behaviour: the gap is bounded, not permanent', blackout > 0 && blackout < 30,
      `${blackout + 5} polls (~${blackout + 5}s) with no estimate`)

const fixedMap = new Map()
let t3 = 0
let fixedBlackout = 0
for (let saved = 10; saved <= 600; saved += 10) {
  t3 += 1000
  sampleProgress(fixedMap, [{ id: 'imp', total: 2169, current: saved }], t3)
  if (etaText(fixedMap, { id: 'imp', total: 2169, current: saved }) === '') fixedBlackout++
  else break
}
// 5 is the floor by design: etaText refuses to extrapolate from less than a
// 5-second window, and polling is 1s. So this is as fast as an honest
// estimate can appear — down from 18.
check('fixed: an estimate appears as soon as honestly possible',
      fixedBlackout <= 6, `${fixedBlackout} polls (floor is 5)`)

// --- the rest ---------------------------------------------------------------
const m2 = new Map()
sampleProgress(m2, [{ id: 'a', total: 100, current: 5 }], 0)
sampleProgress(m2, [{ id: 'a', total: 100, current: 50 }], 10000)
sampleProgress(m2, [{ id: 'a', total: 100, current: 95 }], 20000)
check('steady job reports time left',
      etaText(m2, { id: 'a', total: 100, current: 95 }) !== '')

sampleProgress(m2, [], 21000)
check('finished job drops out of the map', !m2.has('a'))

const m3 = new Map()
sampleProgress(m3, [{ id: 'b', total: 100, current: 1 }], 0)
sampleProgress(m3, [{ id: 'b', total: 100, current: 2 }], 1000)
check('no ETA from too short a window',
      etaText(m3, { id: 'b', total: 100, current: 2 }) === '')

const m4 = new Map()
for (let i = 0; i < 30; i++) sampleProgress(m4, [{ id: 'c', total: 999, current: i }], i * 1000)
check('sample buffer is bounded', m4.get('c').length === 20, `${m4.get('c').length}`)

console.log(failures ? `\n${failures} FAILED` : '\nall passed')
process.exit(failures ? 1 : 0)
