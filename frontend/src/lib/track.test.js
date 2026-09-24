import { describe, expect, it } from 'vitest'
import { makeTracker, normalizeLabel } from './track'

// A scheduler the test drives by hand, so timer behaviour is exact.
function manualClock() {
  const timers = new Map()
  let next = 1
  return {
    schedule: (fn, ms) => { const id = next++; timers.set(id, { fn, ms }); return id },
    cancel: (id) => { timers.delete(id) },
    fire: () => { for (const [id, t] of [...timers]) { timers.delete(id); t.fn() } },
    pending: () => timers.size,
  }
}

function tracker(overrides = {}) {
  const sent = []
  const clock = manualClock()
  const t = makeTracker({
    send: (batch) => { sent.push(batch) },
    schedule: clock.schedule, cancel: clock.cancel,
    maxBatch: 3, flushEvery: 3000,
    ...overrides,
  })
  return { t, sent, clock }
}

describe('normalizeLabel', () => {
  it('makes the same button the same label whatever the count', () => {
    // "Price all 386 with AI" and "Price all 12 with AI" must aggregate.
    expect(normalizeLabel('Price all 386 with AI')).toBe('Price all N with AI')
    expect(normalizeLabel('Price all 12 with AI')).toBe('Price all N with AI')
    expect(normalizeLabel('Clear 1 filter')).toBe('Clear N filter')
    expect(normalizeLabel('Select all 1,199')).toBe('Select all N')
  })

  it('collapses whitespace, trims, and caps the length', () => {
    expect(normalizeLabel('  Comps only,\n   no AI ')).toBe('Comps only, no AI')
    expect(normalizeLabel('x'.repeat(100))).toHaveLength(40)
    expect(normalizeLabel(null)).toBe('')
  })
})

describe('makeTracker', () => {
  it('batches events and sends when the batch is full', () => {
    const { t, sent } = tracker()
    t.track('button', { name: 'a' })
    t.track('button', { name: 'b' })
    expect(sent).toEqual([])                       // not yet - a click never waits
    t.track('button', { name: 'c' })
    expect(sent).toHaveLength(1)
    expect(sent[0].map((e) => e.props.name)).toEqual(['a', 'b', 'c'])
    expect(t.pending()).toBe(0)
  })

  it('sends a short batch when the timer fires', () => {
    const { t, sent, clock } = tracker()
    t.track('filter', { key: 'roi', value: '>100' })
    expect(clock.pending()).toBe(1)
    clock.fire()
    expect(sent).toHaveLength(1)
    expect(sent[0][0]).toMatchObject({ name: 'filter', props: { key: 'roi', value: '>100' } })
  })

  it('arms one timer, not one per event', () => {
    const { t, clock } = tracker()
    t.track('a'); t.track('b')
    expect(clock.pending()).toBe(1)
  })

  it('a full-batch flush cancels the pending timer', () => {
    const { t, clock } = tracker()
    t.track('a'); t.track('b'); t.track('c')
    expect(clock.pending()).toBe(0)
  })

  it('never surfaces a failure to send', () => {
    const { t } = tracker({ send: () => { throw new Error('network down') } })
    expect(() => { t.track('a'); t.track('b'); t.track('c') }).not.toThrow()
    const rejecting = tracker({ send: () => Promise.reject(new Error('500')) })
    expect(() => { rejecting.t.track('a'); rejecting.t.track('b'); rejecting.t.track('c') }).not.toThrow()
  })

  it('tags each event with the view it happened on', () => {
    const { t, sent } = tracker({ view: () => 'items' })
    t.track('a'); t.track('b'); t.track('c')
    expect(sent[0].every((e) => e.view === 'items')).toBe(true)
  })

  it('drain takes what is waiting without sending it', () => {
    // The page-unload path sends by beacon instead of fetch.
    const { t, sent, clock } = tracker()
    t.track('a')
    const batch = t.drain()
    expect(batch.map((e) => e.name)).toEqual(['a'])
    expect(sent).toEqual([])
    expect(t.pending()).toBe(0)
    expect(clock.pending()).toBe(0)
  })

  it('records nothing when disabled or unnamed', () => {
    const { t } = tracker({ enabled: false })
    t.track('a')
    expect(t.pending()).toBe(0)
    const on = tracker()
    on.t.track('')
    expect(on.t.pending()).toBe(0)
  })
})
