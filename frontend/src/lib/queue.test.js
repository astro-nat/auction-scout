import { describe, expect, it } from 'vitest'
import { jobProgress, queueCount, taskLabel } from './queue'

describe('jobProgress', () => {
  it('says how far a running job has got and what is left', () => {
    expect(jobProgress({ state: 'running', current: 12, total: 40, remaining: 28 }))
      .toBe('12 of 40 done · 28 left')
  })

  it('works out what is left when the server did not say', () => {
    expect(jobProgress({ state: 'running', current: 3, total: 10 })).toBe('3 of 10 done · 7 left')
  })

  it('names a job that has not started yet', () => {
    expect(jobProgress({ state: 'pending', current: 0, total: 5, remaining: 5 }))
      .toBe('waiting to start · 5 to do')
  })

  it('copes with a job that has no count', () => {
    expect(jobProgress({ state: 'running', current: 0, total: null })).toBe('working')
    expect(jobProgress({ state: 'pending', total: null })).toBe('waiting to start')
  })
})

describe('taskLabel', () => {
  it('tells a photo read from a plain pricing', () => {
    expect(taskLabel('inspect')).toBe('read the photo and price it')
    expect(taskLabel('enrich')).toBe('price it')
    expect(taskLabel(undefined)).toBe('price it')
  })
})

describe('queueCount', () => {
  it('adds open jobs to waiting lots', () => {
    expect(queueCount({ jobs: [{}, {}], enrichment: { queued: 7 } })).toBe(9)
  })

  it('is zero before the first status arrives', () => {
    expect(queueCount(null)).toBe(0)
    expect(queueCount({})).toBe(0)
  })
})
