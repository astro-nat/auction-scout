// parseUtc guards against the browser reading the backend's naive-UTC
// timestamps as local time — the misread that once hid a 5-hour
// closing-time bug. These run in whatever timezone CI happens to use,
// which is exactly the point.
import { describe, expect, it } from 'vitest'

import { parseUtc } from './time'

describe('parseUtc', () => {
  it('reads a naive timestamp as UTC, not local', () => {
    expect(parseUtc('2026-09-20T04:59:00').getTime())
      .toBe(Date.parse('2026-09-20T04:59:00Z'))
  })

  it('leaves already-zoned timestamps alone', () => {
    expect(parseUtc('2026-09-20T04:59:00Z').getTime())
      .toBe(Date.parse('2026-09-20T04:59:00Z'))
    expect(parseUtc('2026-09-20T04:59:00-05:00').getTime())
      .toBe(Date.parse('2026-09-20T09:59:00Z'))
  })

  it('passes empty values through as null', () => {
    expect(parseUtc(null)).toBeNull()
    expect(parseUtc('')).toBeNull()
    expect(parseUtc(undefined)).toBeNull()
  })
})
