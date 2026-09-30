// A source guard: the row's request must be claimed BEFORE it is sent.
//
// The row button reads "Working…" off pollingIds. These handlers used to add
// the lot to it only after the request came back, so for the whole round
// trip - measured at 1.4 to 2.0 seconds against production - the button
// still read "Further inspect with AI" and was still live.
//
// The usage log shows what that cost: 98 presses of that button, 47% of them
// under five seconds apart, nine pairs inside the SAME second. A second press
// on a lot the worker has already claimed clears claimed_at and re-queues it,
// so the AI call runs twice and is billed twice.
//
// Nothing a person sees goes wrong when this regresses - the button works,
// the lot gets priced - so a behaviour test would not catch it being moved
// back. Hence a guard on the order itself.

import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const SRC = join(dirname(fileURLToPath(import.meta.url)), '..')
const source = readFileSync(join(SRC, 'components', 'LotTable.jsx'), 'utf8')

// Every handler that spends money or queues work on a single lot.
const SPENDERS = ['handleEnrich', 'handleComps', 'handleRecheck']

function bodyOf(name) {
  const start = source.indexOf(`async function ${name}(`)
  expect(start, `${name} not found — renamed?`).toBeGreaterThan(-1)
  // To the start of the next top-level function declaration.
  const rest = source.slice(start + 1)
  const end = rest.search(/\n {2}(?:async )?function /)
  return end === -1 ? rest : rest.slice(0, end)
}

describe('single-lot handlers', () => {
  it.each(SPENDERS)('%s claims the row before it awaits anything', (name) => {
    const body = bodyOf(name)
    const claim = body.indexOf('claimRow(')
    const firstAwait = body.indexOf('await ')
    expect(claim, `${name} must call claimRow()`).toBeGreaterThan(-1)
    expect(firstAwait, `${name} should await something`).toBeGreaterThan(-1)
    expect(claim,
      `${name} awaits before claiming the row — the button stays live for the `
      + `whole round trip and a second press spends another AI call`)
      .toBeLessThan(firstAwait)
  })

  it.each(SPENDERS)('%s releases the row when the request fails', (name) => {
    const body = bodyOf(name)
    expect(body).toMatch(/catch[\s\S]*releaseRow\(/)
  })

  it.each(SPENDERS)('%s refuses a second press while one is in flight', (name) => {
    // claimRow returns false for a lot already claimed; the handler has to
    // act on that rather than carrying on.
    expect(bodyOf(name)).toMatch(/if \(!claimRow\(\w+\)\) return/)
  })
})

describe('the claim itself', () => {
  it('guards on a ref, not on state', () => {
    // Two clicks inside one render both read the same stale state Set. A ref
    // is updated the moment the first one runs.
    const body = source.slice(source.indexOf('function claimRow('))
    expect(body.slice(0, 400)).toMatch(/inFlight\.current\.has\(/)
  })

  it('is released when polling finds the lot settled', () => {
    // Otherwise the row shows "Working…" forever and can never be retried.
    expect(source).toMatch(/status !== 'queued'[\s\S]{0,200}releaseRow\(/)
  })
})
