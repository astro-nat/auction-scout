import { parseUtc } from '../api'

// Lots still worth importing. HiBid's lot_count includes closed lots, which
// an import never brings in - so once a sale starts closing, lot_count minus
// imported climbs with every lot that closes. The backend's count of OPEN
// lots not on file (from the last import or bid refresh) is the real number;
// the old arithmetic stands in only until that first read.
export function missingLots(a, now = new Date()) {
  if (!a || (a.closing_date && parseUtc(a.closing_date) < now)) return 0
  if (a.lots_missing_open != null) return a.lots_missing_open
  return a.lot_count != null && a.lots_imported != null && a.lots_imported < a.lot_count
    ? a.lot_count - a.lots_imported : 0
}
