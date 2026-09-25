import { describe, expect, it } from 'vitest'
import {
  DEFAULT_PAGE_SIZE, pageButtons, pageWindow, savePageSize, savedPageSize,
  searchMatches, showingText,
} from './paging'

describe('pageWindow', () => {
  it('slices the requested page', () => {
    expect(pageWindow(386, 2, 50)).toEqual({ page: 2, pages: 8, start: 50, end: 100 })
    expect(pageWindow(386, 8, 50)).toEqual({ page: 8, pages: 8, start: 350, end: 386 })
  })

  it('clamps a page past the end, as when a filter shrinks the result', () => {
    expect(pageWindow(30, 5, 25)).toEqual({ page: 2, pages: 2, start: 25, end: 30 })
  })

  it('has one empty page when nothing matches', () => {
    expect(pageWindow(0, 3, 50)).toEqual({ page: 1, pages: 1, start: 0, end: 0 })
  })
})

describe('pageButtons', () => {
  it('shows every page when there are few', () => {
    expect(pageButtons(2, 4)).toEqual([1, 2, 3, 4])
  })

  it('keeps first, last and neighbours, with gaps as ellipses', () => {
    expect(pageButtons(10, 20)).toEqual([1, '…', 9, 10, 11, '…', 20])
    expect(pageButtons(1, 20)).toEqual([1, 2, '…', 20])
    expect(pageButtons(20, 20)).toEqual([1, '…', 19, 20])
  })

  it('shows a lone missing page rather than an ellipsis for it', () => {
    expect(pageButtons(4, 8)).toEqual([1, 2, 3, 4, 5, '…', 8])
  })
})

describe('showingText', () => {
  it('reads like DataTables', () => {
    expect(showingText(386, 50, 100)).toBe('Showing 51 to 100 of 386 items')
    expect(showingText(1, 0, 1)).toBe('Showing 1 to 1 of 1 item')
    expect(showingText(0, 0, 0)).toBe('No items match')
  })
})

describe('searchMatches', () => {
  const lot = { lot_number: '214A', title: 'Vintage Pyrex Butterprint Bowl',
                auction_name: 'Dickinson General', category: 'Pyrex',
                enrichment: { enriched_title: 'Pyrex 403 Turquoise Butterprint Mixing Bowl' } }

  it('matches every word anywhere, in any order and case', () => {
    expect(searchMatches(lot, 'butterprint pyrex')).toBe(true)
    expect(searchMatches(lot, 'TURQUOISE')).toBe(true)          // only in the AI title
    expect(searchMatches(lot, 'dickinson 214a')).toBe(true)     // auction and lot number
  })

  it('fails when any word is missing', () => {
    expect(searchMatches(lot, 'pyrex casserole')).toBe(false)
  })

  it('matches everything when empty', () => {
    expect(searchMatches(lot, '  ')).toBe(true)
  })
})

describe('page size preference', () => {
  const store = () => { const d = {}; return { getItem: (k) => d[k] ?? null, setItem: (k, v) => { d[k] = v } } }
  const hostile = { getItem() { throw new Error('no') }, setItem() { throw new Error('no') } }

  it('round-trips a valid size and ignores junk', () => {
    const s = store()
    savePageSize(100, s)
    expect(savedPageSize(s)).toBe(100)
    s.setItem('auctionscout.pageSize', '7')
    expect(savedPageSize(s)).toBe(DEFAULT_PAGE_SIZE)
  })

  it('survives storage that throws', () => {
    expect(savedPageSize(hostile)).toBe(DEFAULT_PAGE_SIZE)
    expect(() => savePageSize(25, hostile)).not.toThrow()
  })
})
