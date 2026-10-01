import { describe, expect, it } from 'vitest'
import { missingLots } from './missing'

const future = new Date(Date.now() + 86400000).toISOString()
const past = new Date(Date.now() - 86400000).toISOString()

describe('missingLots', () => {
  it('counts open lots not on file once the catalogue has been read', () => {
    // 900 on HiBid, 500 imported, but 380 of the gap have already closed
    expect(missingLots({ lot_count: 900, lots_imported: 500, lots_missing_open: 20, closing_date: future })).toBe(20)
  })
  it('says nothing is missing when every open lot is on file', () => {
    expect(missingLots({ lot_count: 900, lots_imported: 500, lots_missing_open: 0, closing_date: future })).toBe(0)
  })
  it('falls back to the catalogue arithmetic before the first read', () => {
    expect(missingLots({ lot_count: 900, lots_imported: 500, closing_date: future })).toBe(400)
  })
  it('never offers an import for a closed sale', () => {
    expect(missingLots({ lot_count: 900, lots_imported: 500, lots_missing_open: 20, closing_date: past })).toBe(0)
  })
})
