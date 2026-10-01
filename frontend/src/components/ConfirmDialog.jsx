import { useEffect, useRef, useState } from 'react'
import { registerAsk } from '../lib/ask'

// The one confirmation dialog. The message's first paragraph is the
// question; the rest is the detail under it.
export default function ConfirmDialog() {
  const [req, setReq] = useState(null)
  const ref = useRef(null)
  const cancelRef = useRef(null)
  const okRef = useRef(null)

  useEffect(() => registerAsk((r) => setReq(r)), [])
  useEffect(() => {
    const d = ref.current
    if (!req || !d) return
    if (!d.open) d.showModal()
    // A delete starts on Cancel, so Enter can't confirm it by accident.
    ;(req.danger ? cancelRef : okRef).current?.focus()
  }, [req])

  const close = (answer) => {
    ref.current?.close()
    req?.resolve(answer)
    setReq(null)
  }

  const [question, ...rest] = (req?.message || '').split('\n\n')
  return (
    <dialog ref={ref} className="confirm-dialog" aria-labelledby="confirm-q"
            onCancel={(ev) => { ev.preventDefault(); close(false) }}>
      {req && (
        <>
          <p id="confirm-q" className="confirm-q">{question}</p>
          {rest.length > 0 && <p className="confirm-body">{rest.join('\n\n')}</p>}
          <div className="confirm-actions">
            <button type="button" ref={cancelRef} data-track="Confirm dialog: cancel"
                    onClick={() => close(false)}>Cancel</button>
            <button type="button" ref={okRef} className={req.danger ? 'danger-solid' : 'primary'}
                    data-track={`Confirm dialog: ${req.confirmLabel}`}
                    onClick={() => close(true)}>{req.confirmLabel}</button>
          </div>
        </>
      )}
    </dialog>
  )
}
