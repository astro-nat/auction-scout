import { useState } from 'react'
import { ask } from '../lib/ask'

// Where drive times are measured from. A pickup trip is the fixed cost the
// "By auction" view is built around, so each auction header says how long
// the drive is; this is where you tell it where you start.

export function formatDrive(minutes) {
  if (minutes == null) return null
  const m = Math.round(Number(minutes))
  if (m < 60) return `${m} min drive`
  const h = Math.floor(m / 60)
  const rest = m % 60
  return rest ? `${h} h ${rest} min drive` : `${h} h drive`
}

export function CarIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor"
         strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"
         style={{ verticalAlign: '-2px' }}>
      <path d="M5 16h14M6.5 16l1.4-5.2A2 2 0 0 1 9.8 9.3h4.4a2 2 0 0 1 1.9 1.5L17.5 16" />
      <path d="M4 16v3h3v-3M17 16v3h3v-3" />
    </svg>
  )
}

export default function DriveFrom({ driveFrom, available, onSave, onClear }) {
  const [address, setAddress] = useState(driveFrom?.address || '')
  const [label, setLabel] = useState(driveFrom?.label || '')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [saved, setSaved] = useState('')

  const summary = driveFrom
    ? `From ${driveFrom.label || driveFrom.address}`
    : 'Set your address'

  async function submit(ev) {
    ev.preventDefault()
    if (!address.trim()) { setError('Enter an address or a zip code.'); return }
    setSaving(true); setError(''); setSaved('')
    try {
      const r = await onSave(address.trim(), label.trim())
      setSaved(`Saved. Matched ${r.matched}; drive times updated for ${r.updated} auctions.`)
    } catch (e) {
      // The server's own reason, without the request prefix.
      setError(String(e.message || e).replace(/^.*failed \(\d+\): /, ''))
    } finally {
      setSaving(false)
    }
  }

  return (
    <details className="picker" style={{ position: 'relative' }}>
      <summary title="Where drive times to each auction are measured from">
        <CarIcon /> {summary} ▾
      </summary>
      <form className="panel" onSubmit={submit}
            style={{ display: 'flex', flexDirection: 'column', gap: 10, minWidth: 300 }}>
        <strong style={{ fontSize: 15 }}>Where do you drive from?</strong>
        {!available && (
          <span style={{ fontSize: 13, color: 'var(--warn)' }}>
            Drive times need an OpenRouteService key on the server (ORS_API_KEY).
          </span>
        )}
        <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 13 }}>
          Address or zip
          <input type="text" value={address} autoComplete="street-address"
                 onChange={(ev) => setAddress(ev.target.value)} />
        </label>
        <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 13 }}>
          <span>Name <span style={{ color: 'var(--muted)' }}>(optional, like Home)</span></span>
          <input type="text" value={label} maxLength={40}
                 onChange={(ev) => setLabel(ev.target.value)} />
        </label>
        <span style={{ fontSize: 12, color: 'var(--muted)' }}>
          Drive times are one way, for typical traffic. The address is sent to
          OpenRouteService to find it on the map.
        </span>
        {error && <span role="alert" style={{ fontSize: 13, color: 'var(--error)' }}>{error}</span>}
        {saved && <span role="status" style={{ fontSize: 13, color: 'var(--success)' }}>{saved}</span>}
        <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
          {driveFrom && (
            <button type="button" className="danger" data-track="Forget drive-from address"
                    onClick={async () => {
                      if (!(await ask('Forget your drive-from address?\n\nDrive times disappear until you set one again.',
                                      { confirmLabel: 'Forget it', danger: true }))) return
                      await onClear()
                      setAddress(''); setLabel(''); setSaved('')
                    }}>
              Forget address
            </button>
          )}
          <button type="submit" className="primary" disabled={saving || !available}
                  data-track="Save drive-from address">
            {saving ? <><span className="spinner" />Measuring…</> : 'Save address'}
          </button>
        </div>
      </form>
    </details>
  )
}
