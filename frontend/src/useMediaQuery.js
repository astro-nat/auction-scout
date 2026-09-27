import { useEffect, useState } from 'react'

export default function useMediaQuery(query) {
  const [matches, setMatches] = useState(() => window.matchMedia(query).matches)

  useEffect(() => {
    const mq = window.matchMedia(query)
    // Re-read before subscribing. The first read happens during render and
    // the listener is attached after it, so a change in between was lost for
    // good: the app kept the phone layout at 1500px wide, tables and all,
    // until a full reload. Anyone who resizes, docks or rotates a window can
    // land in that gap.
    setMatches(mq.matches)
    const onChange = (ev) => setMatches(ev.matches)
    mq.addEventListener('change', onChange)
    return () => mq.removeEventListener('change', onChange)
  }, [query])

  return matches
}
