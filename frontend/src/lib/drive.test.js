import { describe, expect, it } from 'vitest'
import { formatDrive } from '../components/DriveFrom'

describe('formatDrive', () => {
  it('reads as minutes under an hour and hours past it', () => {
    expect(formatDrive(null)).toBe(null)
    expect(formatDrive(11.4)).toBe('11 min drive')
    expect(formatDrive(59.6)).toBe('1 h drive')
    expect(formatDrive(84)).toBe('1 h 24 min drive')
    expect(formatDrive(120)).toBe('2 h drive')
  })
})
