import { useEffect, useState } from 'react'

export interface Kpi {
  median: number | null; medianYoY: number | null; ppsf: number | null; ppsfYoY: number | null
  sales12: number; salesYoY: number | null; median12: number | null
}
export interface Source { name: string; publisher: string; url: string }
export interface City {
  id: string; name: string; state: string; center: [number, number]; zoom: number; areaNoun: string; notes: string
  sources: Source[]; rows: number; firstSale: string; latestSale: string; through: string; areas: number
  kpi: Kpi; spark: (number | null)[]
  coverage: { zip: number; area: number; coordinates: number; sqft: number }
  context: Context; crimeSource: string; crimeLink: string
  /** Sales held per sale month, latest 24 months, including months after `through` that are still filling in. */
  byMonth: { month: string; count: number }[]
}
export interface Context {
  crimeAreas: number; rentAreas: number; residents: number | null; crimeThrough: string | null
  violentRate: number | null; propertyRate: number | null; crimeYoY: number | null
  crimeTrend?: 'up' | 'down' | 'flat' | null   // 'flat' = change not statistically distinguishable from none
  rent: number | null; rentYoY: number | null; rentMonth: string | null; yield: number | null
  listingAreas?: number
}
/** Asking-side figures from Zillow Research by ZIP code (homes and condos). Never mixed with recorded sales. */
export interface Listings {
  month: string; coverage: number                                // % of the area's recent sales in ZIPs with figures
  active: number | null; activeYoY: number | null                // for-sale inventory; for an area, ZIP counts shared out by its sales
  newListings: number | null; newListingsYoY: number | null
  listPrice: number | null; listPriceYoY: number | null          // median list price, sales-weighted across ZIPs
  priceCutShare: number | null; priceCutShareYoY: number | null  // % of listings with a price cut; YoY in percentage points
  series?: { months: string[]; active: number[] | null; listPrice: (number | null)[] } | null   // citywide only, latest 13 months
}
export interface Grid {
  city: string; cell: number; crimeThrough: string | null
  columns: { lat: number[]; lon: number[]; violent: number[]; property: number[]; before: number[]; price: (number | null)[]; sales: number[]; area: number[] }   // area = index into areas.json, -1 if none
}
export interface Index { generatedAt: string; cities: City[] }

export interface Series { median: (number | null)[]; p25: (number | null)[]; p75: (number | null)[]; count: number[]; ppsf: (number | null)[] }
export interface Overview {
  city: string; name: string; through: string; latestSale: string; months: string[]; typeOrder: string[]
  types: Record<string, { series: Series; kpi: Kpi; hist: { edges: number[]; counts: number[] } }>
  listings?: Listings | null
}
export interface Area {
  key: string; name: string; group: string; lat: number | null; lon: number | null
  sales12: number; salesYoY: number | null; median12: number | null; medianYoY: number | null
  ppsf12: number | null; change5y: number | null; topType: string; topTypeShare: number; quarterly: (number | null)[]
  residents: number | null; violentRate: number | null; propertyRate: number | null; crimeYoY: number | null
  rent: number | null; rentYoY: number | null; yield: number | null
  zips: string[]
  // statistically adjusted inputs (see engine/analytics.py); optional until the engine publishes them
  priceGrowth1?: number | null; priceGrowth5?: number | null   // best available growth: repeat-sales index, else change in median
  growthMethod?: 'repeat' | 'median' | null; repeatPairs?: number | null
  medianLow?: number | null; medianHigh?: number | null         // 95% interval for the 12-month median
  netYield?: number | null                                      // rule-of-thumb yield after typical running costs
  violentRaw?: number | null; propertyRaw?: number | null       // unadjusted rates; violentRate/propertyRate are shrunk toward the city rate
  crimeTrend?: 'up' | 'down' | 'flat' | null                    // 'flat' = change not statistically distinguishable from none
  listings?: Listings | null                                    // omitted where under 60% of the area's sales lie in covered ZIPs
}
export interface Areas { city: string; through: string; quarters: string[]; areas: Area[] }
export interface Sales {
  city: string; from: string | null; latestSale: string; typeNames: string[]; count: number
  columns: {
    date: string[]; price: number[]; type: number[]; address: string[]; zip: string[]; area: number[]
    sqft: (number | null)[]; yb: (number | null)[]; beds?: (number | null)[]; lat: (number | null)[]; lon: (number | null)[]
    vs: (number | null)[]; basis: (number | null)[]; ratio?: (number | null)[]
  }
}
export interface SourceReport {
  name: string; dataset: string; fetched: number; kept: number; new: number; updated: number; unchanged: number
  rejected: Record<string, number>; skipped: string | null; requests: number; context?: boolean
  last?: { fetched: number; kept: number; rejected: Record<string, number>; date: string } | null
}
export interface Check { city: string; name: string; status: 'pass' | 'warn' | 'fail'; detail: string }
export interface Run {
  run_id: string; started: string; finished: string; duration_s: number; mode: string; ok: boolean
  cities: Record<string, { mode: string; since: string; sources: SourceReport[]; seconds: number; error: string | null; rows: number }>
  checks: Check[]; http: { requests: number; retries: number; bytes: number }
}
export interface RunSummary {
  id: string; started: string; duration: number; mode: string; ok: boolean; requests: number; retries: number
  new: number; updated: number; fetched: number; warnings: number; failures: number
}
export interface Status { generatedAt: string; last: Run | null; history: RunSummary[] }

const cache = new Map<string, Promise<unknown>>()

function load<T>(path: string): Promise<T> {
  if (!cache.has(path)) {
    cache.set(path, fetch(`data/${path}`).then((r) => {
      if (!r.ok) throw new Error(`Could not load ${path} (${r.status})`)
      return r.json()
    }))
  }
  return cache.get(path) as Promise<T>
}

export function useData<T>(path: string | null): { data: T | null; error: string | null } {
  const [state, setState] = useState<{ path: string | null; data: T | null; error: string | null }>({ path: null, data: null, error: null })
  useEffect(() => {
    if (!path) return
    let live = true
    load<T>(path).then(
      (data) => live && setState({ path, data, error: null }),
      (e) => live && setState({ path, data: null, error: String(e.message || e) }),
    )
    return () => { live = false }
  }, [path])
  return state.path === path ? { data: state.data, error: state.error } : { data: null, error: null }
}
