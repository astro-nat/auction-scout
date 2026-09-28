import { useState } from 'react'
import { addNeverRule, deleteNeverRule, patchNeverRule, previewNeverRule, alertOnce } from '../api'

// The never list: kinds of thing not worth seeing. The inverse of BOLO.
//
// It is an editor rather than a file in the repo because the user keeps
// thinking of new ones - "nothing sharp", "no chandeliers or vacuum
// cleaners" - and waiting for a push each time is the problem it exists to
// solve. Every rule carries its own exceptions, because every word that
// names a thing also names something else: "blade" is Blade Runner, "razor"
// is a scooter, a souvenir hatchet has no edge on it.

const asList = (text) =>
  text.split(',').map((p) => p.trim()).filter(Boolean)

const asText = (list) => (list || []).join(', ')

const box = { padding: '2px 4px', width: '100%', boxSizing: 'border-box' }

function Preview({ result }) {
  if (!result) return null
  if (result.error) return <div style={{ color: 'var(--bad)' }}>{result.error}</div>
  return (
    <div style={{ fontSize: 12, color: 'var(--muted)', marginTop: 4 }}>
      Would hide {result.matched.toLocaleString()} lot{result.matched === 1 ? '' : 's'}
      {result.titles.length > 0 && (
        <ul style={{ margin: '4px 0 0', paddingLeft: 18 }}>
          {result.titles.slice(0, 8).map((t, i) => <li key={i}>{t}</li>)}
        </ul>
      )}
    </div>
  )
}

function Row({ rule, onChanged }) {
  const [phrases, setPhrases] = useState(asText(rule.phrases))
  const [excepts, setExcepts] = useState(asText(rule.except_phrases))
  const [busy, setBusy] = useState(false)
  const dirty = phrases !== asText(rule.phrases) || excepts !== asText(rule.except_phrases)

  const run = async (fn) => {
    setBusy(true)
    try { await fn(); await onChanged() }
    catch (err) { alertOnce(err.message) }
    finally { setBusy(false) }
  }

  return (
    <tr>
      <td style={{ verticalAlign: 'top' }}>
        <label style={{ display: 'inline-flex', gap: 4, alignItems: 'center' }}>
          <input
            type="checkbox"
            checked={rule.enabled}
            disabled={busy}
            data-track="Toggle never rule"
            onChange={(ev) => run(() => patchNeverRule(rule.id, { enabled: ev.target.checked }))}
          /> {rule.label}
        </label>
      </td>
      <td>
        <input style={box} value={phrases} disabled={busy}
               onChange={(ev) => setPhrases(ev.target.value)} />
      </td>
      <td>
        <input style={box} value={excepts} disabled={busy}
               onChange={(ev) => setExcepts(ev.target.value)} />
      </td>
      <td style={{ whiteSpace: 'nowrap', verticalAlign: 'top' }}>
        <button
          disabled={busy || !dirty}
          data-track="Save never rule"
          onClick={() => run(() => patchNeverRule(rule.id, {
            phrases: asList(phrases), except_phrases: asList(excepts),
          }))}
        >Save</button>{' '}
        <button
          disabled={busy}
          data-track="Delete never rule"
          onClick={() => {
            if (!window.confirm(`Remove "${rule.label}" from the never list?`)) return
            run(() => deleteNeverRule(rule.id))
          }}
        >Remove</button>
      </td>
    </tr>
  )
}

export default function NeverListEditor({ rules, onChanged }) {
  const [label, setLabel] = useState('')
  const [phrases, setPhrases] = useState('')
  const [excepts, setExcepts] = useState('')
  const [result, setResult] = useState(null)
  const [busy, setBusy] = useState(false)

  const draft = () => ({
    label: label.trim() || 'Untitled',
    phrases: asList(phrases),
    except_phrases: asList(excepts),
  })

  const check = async () => {
    if (asList(phrases).length === 0) { setResult({ error: 'Add a word to match first' }); return }
    setBusy(true)
    try { setResult(await previewNeverRule(draft())) }
    catch (err) { setResult({ error: err.message }) }
    finally { setBusy(false) }
  }

  const save = async () => {
    if (!label.trim()) { setResult({ error: 'Give it a name' }); return }
    if (asList(phrases).length === 0) { setResult({ error: 'Add a word to match first' }); return }
    setBusy(true)
    try {
      await addNeverRule(draft())
      setLabel(''); setPhrases(''); setExcepts(''); setResult(null)
      await onChanged()
    } catch (err) { setResult({ error: err.message }) }
    finally { setBusy(false) }
  }

  return (
    <div style={{ border: '1px solid var(--border)', borderRadius: 6, padding: 10,
                  marginTop: 8, maxWidth: 900 }}>
      <div style={{ fontSize: 12, color: 'var(--muted)', marginBottom: 6 }}>
        Things you never want to see. Words are matched whole, so "axe" does not
        match "faxed". A lot matching a word is kept anyway if it also matches
        one of the exceptions - that is what stops "blade" hiding Blade Runner.
      </div>
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
        <thead>
          <tr style={{ textAlign: 'left', color: 'var(--muted)' }}>
            <th style={{ width: '22%' }}>Category</th>
            <th style={{ width: '33%' }}>Words to match</th>
            <th style={{ width: '33%' }}>Except when it says</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {rules.map((r) => (
            <Row key={r.id} rule={r} onChanged={onChanged} />
          ))}
          <tr>
            <td style={{ verticalAlign: 'top' }}>
              <input style={box} placeholder="Exercise equipment" value={label}
                     disabled={busy} onChange={(ev) => setLabel(ev.target.value)} />
            </td>
            <td style={{ verticalAlign: 'top' }}>
              <input style={box} placeholder="treadmill, elliptical" value={phrases}
                     disabled={busy} onChange={(ev) => setPhrases(ev.target.value)} />
            </td>
            <td style={{ verticalAlign: 'top' }}>
              <input style={box} placeholder="treadmill desk" value={excepts}
                     disabled={busy} onChange={(ev) => setExcepts(ev.target.value)} />
            </td>
            <td style={{ whiteSpace: 'nowrap', verticalAlign: 'top' }}>
              <button disabled={busy} data-track="Preview never rule"
                      onClick={check}>Preview</button>{' '}
              <button disabled={busy} data-track="Add never rule"
                      onClick={save}>Add</button>
            </td>
          </tr>
        </tbody>
      </table>
      <Preview result={result} />
    </div>
  )
}
