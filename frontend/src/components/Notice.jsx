import { useEffect, useState } from 'react'
import { onNotice } from '../lib/notice'

const SHOW_MS = 7000

// The latest notice, bottom of the screen, gone after a few seconds or on
// a click. Announced politely to screen readers.
export default function Notice() {
  const [text, setText] = useState(null)
  useEffect(() => onNotice(setText), [])
  useEffect(() => {
    if (!text) return undefined
    const t = setTimeout(() => setText(null), SHOW_MS)
    return () => clearTimeout(t)
  }, [text])
  return (
    <div className="notice-wrap" role="status" aria-live="polite">
      {text && (
        <div className="notice">
          <span style={{ whiteSpace: 'pre-line' }}>{text}</span>
          <button type="button" className="bare icon-btn" aria-label="Dismiss"
                  data-track="Dismiss notice" onClick={() => setText(null)}>✕</button>
        </div>
      )}
    </div>
  )
}
