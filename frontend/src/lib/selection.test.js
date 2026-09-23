import { describe, expect, it } from 'vitest'
import { allSelected, chunked, inView, selectAll, toggle } from './selection'

const lots = (...ids) => ids.map((id) => ({ lot_id: id }))

describe('toggle', () => {
  it('adds an unselected id and removes a selected one', () => {
    const a = toggle(new Set(), 'x')
    expect([...a]).toEqual(['x'])
    expect([...toggle(a, 'x')]).toEqual([])
  })

  it('never mutates the set React holds', () => {
    const held = new Set(['a'])
    toggle(held, 'b')
    expect([...held]).toEqual(['a'])
  })
})

describe('selectAll', () => {
  it('covers the whole result, not just the rows on screen', () => {
    // The render cap paints 150 rows; "select all" on a 1,199-lot result
    // must mean 1,199, the same rule the whole-result Price button uses.
    const many = lots(...Array.from({ length: 1199 }, (_, i) => `l${i}`))
    expect(selectAll(many).size).toBe(1199)
  })
})

describe('inView', () => {
  it('acts only on ticked lots still in the current result', () => {
    // Ticks survive a filter change, but a lot the filter now hides is
    // not acted on - the user cannot see what they would be doing to it.
    const selected = new Set(['a', 'b', 'c'])
    const visible = lots('a', 'c', 'd')
    expect(inView(selected, visible).map((l) => l.lot_id)).toEqual(['a', 'c'])
  })

  it('is empty when nothing ticked is visible', () => {
    expect(inView(new Set(['zzz']), lots('a', 'b'))).toEqual([])
  })
})

describe('allSelected', () => {
  it('is true only when every visible lot is ticked', () => {
    expect(allSelected(new Set(['a', 'b']), lots('a', 'b'))).toBe(true)
    expect(allSelected(new Set(['a']), lots('a', 'b'))).toBe(false)
  })

  it('is false for an empty result, so the header box is never pre-checked', () => {
    expect(allSelected(new Set(), [])).toBe(false)
  })
})

describe('chunked', () => {
  it('splits into groups of the given size, last one short', () => {
    expect(chunked([1, 2, 3, 4, 5], 2)).toEqual([[1, 2], [3, 4], [5]])
    expect(chunked([], 4)).toEqual([])
  })
})
