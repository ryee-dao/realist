/** Per-area files written by engine/area_trends.py. Recorded sales only; asking prices never enter these. */

/** <city>/area-series.json: monthly figures for each area listed in areas.json, keyed by area key. */
export interface AreaSeries {
  city: string; through: string; months: string[]; rollMonths: number
  areas: Record<string, {
    count: number[]                  // homes sold in each month
    median: (number | null)[]        // median sale price over the rollMonths to each month
    ppsf: (number | null)[]          // same, price per sq ft
    kpi: {
      sales12: number; salesYoY: number | null; median12: number | null; medianYoY: number | null
      ppsf12: number | null; ppsfYoY: number | null; p25: number | null; p75: number | null
      thin: boolean                   // too few sales in 12 months for steady figures
    }
    hist: { step: number; counts: number[] }   // last 12 months, bands of `step` dollars, the last open-ended
  }>
}

export interface SoldSale { date: string; price: number; type: string; address: string; sqft: number | null }
export interface SoldArea {
  key: string; n: number; nBefore: number; salesChange: number | null
  pace: 'up' | 'down' | 'flat' | null   // up/down only when the change is unlikely to be chance
  median: number | null; p25: number | null; p75: number | null; ppsf: number | null; medianYoY: number | null
  latestSale: string | null; latest: SoldSale[]
}
/** <city>/sold.json: the latest `months` complete months (from..through) vs the same months a year earlier. */
export interface Sold { city: string; through: string; from: string; months: number; before: [string, string]; areas: SoldArea[] }

/** Area picked on the Market page, kept in the hash after '?' (#/city/market?area=...). */
export function hashParam(name: string): string {
  const q = window.location.hash.split('?')[1] ?? ''
  return new URLSearchParams(q).get(name) ?? ''
}

export function setHashParam(name: string, value: string) {
  const [path, q] = window.location.hash.split('?')
  const params = new URLSearchParams(q ?? '')
  if (value) params.set(name, value)
  else params.delete(name)
  const s = params.toString()
  // replaceState does not fire hashchange, so the page keeps its scroll position
  history.replaceState(null, '', `${window.location.pathname}${window.location.search}${path}${s ? `?${s}` : ''}`)
}
