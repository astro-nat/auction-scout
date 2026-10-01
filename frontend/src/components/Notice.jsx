import { useEffect, useState } from 'react'
import { onNotice } from '../lib/notice'

const SHOW_MS = 7000
const SHOW_ACTION_MS = 15000   // long enough to read and decide

// The latest notice, bottom of the screen, gone after a few seconds or on
// a click. Announced politely to screen readers.
export default function Notice() {
  const [n, setN] = useState(null)
  useEffect(() => onNotice(setN), [])
  useEffect(() => {
    if (!n) return undefined
    const t = setTimeout(() => setN(null), n.action ? SHOW_ACTION_MS : SHOW_MS)
    return () => clearTimeout(t)
  }, [n])
  return (
    <div className="notice-wrap" role="status" aria-live="polite">
      {n && (
        <div className="notice">
          <span style={{ whiteSpace: 'pre-line' }}>{n.text}</span>
          {n.action && (
            <button type="button" className="primary" data-track={`Notice: ${n.action.label}`}
                    onClick={() => { setN(null); n.action.onClick() }}>{n.action.label}</button>
          )}
          <button type="button" className="bare icon-btn" aria-label="Dismiss"
                  data-track="Dismiss notice" onClick={() => setN(null)}>✕</button>
        </div>
      )}
    </div>
  )
}
