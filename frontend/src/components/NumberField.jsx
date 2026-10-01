import { useEffect, useState } from 'react'

// A number input you can clear and retype. Bound straight to a number, an
// emptied field either snapped to 0 or refused the keystroke, so the only
// way to change "25" to "40" was to select the 2 and type over it.
// Commits each valid number as you type; a blank or invalid entry goes
// back to the last good value when you leave the field.
export default function NumberField({ value, onCommit, min = 0, ...rest }) {
  const [draft, setDraft] = useState(String(value ?? ''))
  useEffect(() => { setDraft(String(value ?? '')) }, [value])
  const valid = (t) => t.trim() !== '' && Number.isFinite(Number(t)) && Number(t) >= min
  return (
    <input type="number" min={min} {...rest} value={draft}
           onChange={(ev) => {
             setDraft(ev.target.value)
             if (valid(ev.target.value)) onCommit(Number(ev.target.value))
           }}
           onBlur={() => { if (!valid(draft)) setDraft(String(value ?? '')) }} />
  )
}
