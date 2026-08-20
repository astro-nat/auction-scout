import { useEffect, useState } from 'react'
import { fetchLots } from './api'
import LotTable from './components/LotTable'

export default function App() {
  const [lots, setLots] = useState([])

  useEffect(() => {
    fetchLots().then(setLots).catch(console.error)
  }, [])

  function handleLotUpdated(updated) {
    setLots((prev) => prev.map((l) => (l.lot_id === updated.lot_id ? updated : l)))
  }

  return (
    <div style={{ padding: '2rem', fontFamily: 'system-ui' }}>
      <h1>AuctionScout</h1>
      <LotTable lots={lots} onLotUpdated={handleLotUpdated} />
    </div>
  )
}
