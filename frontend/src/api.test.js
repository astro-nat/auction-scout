// request() retries a transient failure quietly instead of surfacing a
// dead-end popup on the first blip — a deploy restart or a 502 is usually
// gone within a few seconds.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { fetchCategories } from './api'

function jsonResponse(body, status = 200) {
  return { ok: status >= 200 && status < 300, status, json: async () => body }
}

beforeEach(() => { vi.useFakeTimers() })
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals() })

async function runWithFakeTimers(makePromise) {
  const promise = makePromise()
  // A rejection that later tests assert on via .rejects would otherwise be
  // "unhandled" for the ticks between creation and that assertion, since
  // nothing has attached a handler yet while the timers advance below.
  promise.catch(() => {})
  // Let the promise chain progress, then fast-forward past every backoff
  // sleep it might be waiting on, repeating until it actually settles.
  for (let i = 0; i < 5; i++) {
    await Promise.resolve()
    await vi.advanceTimersByTimeAsync(5000)
  }
  return promise
}

describe('request retry', () => {
  it('succeeds immediately when the server is up', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse([{ id: 1 }]))
    vi.stubGlobal('fetch', fetchMock)
    const result = await runWithFakeTimers(() => fetchCategories())
    expect(result).toEqual([{ id: 1 }])
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('retries a network failure and succeeds once the server comes back', async () => {
    const fetchMock = vi.fn()
      .mockRejectedValueOnce(new TypeError('Failed to fetch'))
      .mockRejectedValueOnce(new TypeError('Failed to fetch'))
      .mockResolvedValueOnce(jsonResponse([{ id: 2 }]))
    vi.stubGlobal('fetch', fetchMock)
    const result = await runWithFakeTimers(() => fetchCategories())
    expect(result).toEqual([{ id: 2 }])
    expect(fetchMock).toHaveBeenCalledTimes(3)
  })

  it('retries a 503 (deploy in progress) the same way', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse(null, 503))
      .mockResolvedValueOnce(jsonResponse([{ id: 3 }]))
    vi.stubGlobal('fetch', fetchMock)
    const result = await runWithFakeTimers(() => fetchCategories())
    expect(result).toEqual([{ id: 3 }])
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it('gives up after exhausting retries and names what it was trying', async () => {
    const fetchMock = vi.fn().mockRejectedValue(new TypeError('Failed to fetch'))
    vi.stubGlobal('fetch', fetchMock)
    await expect(runWithFakeTimers(() => fetchCategories())).rejects.toThrow(
      /Can't reach the server right now \(GET \/auctions\/categories\)/)
    expect(fetchMock).toHaveBeenCalledTimes(4)   // first try + 3 retries
  })

  it('does not retry a genuine application error (422, etc)', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({ detail: 'bad input' }, 422))
    vi.stubGlobal('fetch', fetchMock)
    await expect(runWithFakeTimers(() => fetchCategories())).rejects.toThrow(/bad input/)
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })
})
