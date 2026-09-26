// A guard on the NAMES the usage log records, not on behaviour.
//
// Twice a change of mine renamed a metric without touching anything a person
// could see go wrong. Adding "(132 of 4,701)" beside "Gold mines only" so the
// count could be read without toggling turned the event name into "Gold mines
// only (N of N)", and split it from its own history. The test suite was green
// throughout, because nothing was broken - only the measurement.
//
// A checkbox is the case worth guarding. It has no text of its own: the
// tracker names it from the label wrapped around it (lib/track.checkboxName),
// so editing that label renames the metric. A button at least carries its own
// words. So the rule here is narrow and satisfiable:
//
//   a checkbox whose label text is built from the data must name itself
//   with data-track.
//
// A label of fixed words needs nothing: it can only change when somebody
// edits it, and then the rename is right there in the diff.

import { readFileSync, readdirSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const SRC = join(dirname(fileURLToPath(import.meta.url)), '..')

function jsxFiles(dir) {
  return readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
    const p = join(dir, e.name)
    if (e.isDirectory()) return jsxFiles(p)
    return e.name.endsWith('.jsx') ? [p] : []
  })
}

// Walk out of a JSX tag, ignoring a '>' that sits inside braces or quotes —
// every handler here is an arrow function, and a naive regex reads
// `onChange={() => f()}` as the end of the tag.
function scanPastTag(src, start) {
  let depth = 0
  let quote = null
  for (let i = start; i < src.length; i += 1) {
    const c = src[i]
    if (quote) { if (c === quote) quote = null; continue }
    if (c === '"' || c === "'") { quote = c; continue }
    if (c === '{') depth += 1
    else if (c === '}') depth -= 1
    else if (c === '>' && depth === 0) return i + 1
  }
  return src.length
}

function stripTags(src) {
  let out = ''
  for (let i = 0; i < src.length;) {
    if (src[i] === '<') i = scanPastTag(src, i)
    else { out += src[i]; i += 1 }
  }
  return out
}

function checkboxes() {
  const found = []
  for (const file of jsxFiles(SRC)) {
    const src = readFileSync(file, 'utf8')
    for (let i = src.indexOf('<input'); i !== -1; i = src.indexOf('<input', i + 1)) {
      const attrs = src.slice(i, scanPastTag(src, i))
      if (!attrs.includes('checkbox')) continue
      const line = src.slice(0, i).split('\n').length
      const named = /data-track|aria-label|title=/.test(attrs)
      // The label wrapped around it, when this input is the only one in it.
      const lStart = src.lastIndexOf('<label', i)
      const lEnd = src.indexOf('</label>', i)
      let text = ''
      if (lStart !== -1 && lEnd !== -1) {
        const block = src.slice(lStart, lEnd)
        // Drop JSX comments before reading the words.
        text = stripTags(block.replace(/\{\/\*[\s\S]*?\*\/\}/g, '')).trim()
      }
      found.push({ where: `${file.split(/[\/]/).pop()}:${line}`, named, text })
    }
  }
  return found
}

describe('usage-log names', () => {
  const boxes = checkboxes()

  it('finds the checkboxes to check', () => {
    expect(boxes.length).toBeGreaterThan(5)
  })

  it('every checkbox with a data-built label names itself', () => {
    const unnamed = boxes
      .filter((b) => !b.named && b.text.includes('{'))
      .map((b) => `${b.where} — label is built from data: ${JSON.stringify(b.text.slice(0, 60))}`)
    expect(unnamed, 'add data-track="<the name the log already uses>" to these')
      .toEqual([])
  })

  it('every checkbox is named by something', () => {
    // Otherwise it records as the bare fallback "checkbox" — 21 events in the
    // log say nothing about which box they came from.
    const anonymous = boxes
      .filter((b) => !b.named && !b.text)
      .map((b) => b.where)
    expect(anonymous, 'give these a data-track, aria-label or title').toEqual([])
  })
})
