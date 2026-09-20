// The import-ETA arithmetic, converted from frontend/test/progress.test.mjs
// (a bare-Node script from before the project had a test runner). The bug it
// covers — the ETA vanishing at an import's fetch/save phase flip — renders
// as 'nothing' in the UI and goes unnoticed, which is exactly why it's
// pinned. Cases and numbers preserved from the original.
import { describe, expect, it } from 'vitest'

import { etaText, sampleProgress } from './progress.js'

// Phase 1: fetch, 100 lots per page, ~1s per page, total = HiBid's count.
// Phase 2: save — current RESETS to 0 and total changes to the open-lot count.
function importUpToPhaseFlip() {
  const map = new Map()
  let t = 0
  for (let fetched = 100; fetched <= 1200; fetched += 100) {
    t += 1000
    sampleProgress(map, [{ id: 'imp', total: 2400, current: fetched }], t)
  }
  return { map, t }
}

describe('an import, as it actually behaves', () => {
  it('shows an ETA during the fetch phase', () => {
    const { map } = importUpToPhaseFlip()
    expect(etaText(map, { id: 'imp', total: 2400, current: 1200 })).not.toBe('')
  })

  it('restarts the sample series at the phase flip', () => {
    const { map, t } = importUpToPhaseFlip()
    sampleProgress(map, [{ id: 'imp', total: 2169, current: 0 }], t + 1000)
    expect(map.get('imp')).toHaveLength(1)
  })

  it('shows an ETA during the save phase (the original bug)', () => {
    const { map, t } = importUpToPhaseFlip()
    let now = t + 1000
    sampleProgress(map, [{ id: 'imp', total: 2169, current: 0 }], now)
    for (let saved = 10; saved <= 200; saved += 10) {
      now += 1000
      sampleProgress(map, [{ id: 'imp', total: 2169, current: saved }], now)
    }
    const eta = etaText(map, { id: 'imp', total: 2169, current: 200 })
    expect(eta).not.toBe('')
    expect(eta).toContain('min left')   // ~33 min at 10 lots/s over 1969 left
  })
})

describe('what the old single-series code did, for contrast', () => {
  // Faithful replay: one series, no reset, 20-sample cap. The bug is not a
  // permanently missing ETA — it vanishes right after the flip, then comes
  // back once the cap flushes the fetch samples out.
  function naivePush(map, t, current, total) {
    const arr = map.get('imp') ?? []
    arr.push({ t, current, total })
    while (arr.length > 20) arr.shift()
    map.set('imp', arr)
  }

  it('lost the ETA just after the flip, for a bounded gap', () => {
    const naive = new Map()
    let t = 0
    for (let fetched = 100; fetched <= 1200; fetched += 100) {
      t += 1000
      naivePush(naive, t, fetched, 2400)
    }
    t += 1000
    naivePush(naive, t, 0, 2169)
    for (let saved = 10; saved <= 50; saved += 10) {
      t += 1000
      naivePush(naive, t, saved, 2169)
    }
    expect(etaText(naive, { id: 'imp', total: 2169, current: 50 })).toBe('')

    // Status polls once a second, so every blacked-out sample is about a
    // second of the user staring at a bar with no estimate on it.
    let blackout = 0
    for (let saved = 60; saved <= 600; saved += 10) {
      t += 1000
      naivePush(naive, t, saved, 2169)
      if (etaText(naive, { id: 'imp', total: 2169, current: saved }) === '') blackout++
      else break
    }
    expect(blackout).toBeGreaterThan(0)
    expect(blackout).toBeLessThan(30)
  })

  it('fixed: an estimate appears as soon as honestly possible', () => {
    // 5s is the floor by design — etaText refuses to extrapolate from less
    // than a 5-second window, and polling is 1s. Down from ~18 polls.
    const map = new Map()
    let t = 0
    let blackout = 0
    for (let saved = 10; saved <= 600; saved += 10) {
      t += 1000
      sampleProgress(map, [{ id: 'imp', total: 2169, current: saved }], t)
      if (etaText(map, { id: 'imp', total: 2169, current: saved }) === '') blackout++
      else break
    }
    expect(blackout).toBeLessThanOrEqual(6)
  })
})

describe('the rest of the contract', () => {
  it('a steady job reports time left', () => {
    const m = new Map()
    sampleProgress(m, [{ id: 'a', total: 100, current: 5 }], 0)
    sampleProgress(m, [{ id: 'a', total: 100, current: 50 }], 10000)
    sampleProgress(m, [{ id: 'a', total: 100, current: 95 }], 20000)
    expect(etaText(m, { id: 'a', total: 100, current: 95 })).not.toBe('')
    sampleProgress(m, [], 21000)
    expect(m.has('a')).toBe(false)      // finished job drops out of the map
  })

  it('refuses an ETA from too short a window', () => {
    const m = new Map()
    sampleProgress(m, [{ id: 'b', total: 100, current: 1 }], 0)
    sampleProgress(m, [{ id: 'b', total: 100, current: 2 }], 1000)
    expect(etaText(m, { id: 'b', total: 100, current: 2 })).toBe('')
  })

  it('bounds the sample buffer', () => {
    const m = new Map()
    for (let i = 0; i < 30; i++) {
      sampleProgress(m, [{ id: 'c', total: 999, current: i }], i * 1000)
    }
    expect(m.get('c')).toHaveLength(20)
  })
})
