import '../styles/finder.css'
import '../styles/areas.css'
import { Fragment, ReactNode, startTransition, useEffect, useMemo, useRef, useState } from 'react'
import { MiniBar, Tip } from '../components/MiniBar'
import { Area, Areas, City, Listings, useData } from '../lib/data'
import { Theme, cssVar } from '../lib/hooks'
import { monthLabel, num, pct, usd, usdCompact } from '../lib/format'
import { rentLines, rentValue, yieldRange } from '../lib/rent'
import MapView, { classOf, MapPoint, quintiles, SCALE } from '../components/MapView'
import LineChart from '../charts/LineChart'
import Sparkline from '../charts/Sparkline'
import { revealOnPhone, Segmented, SortHead } from '../components/bits'
import { activityPercentiles, blendOf, gradeOf, gradeStyle, percentiles, Ramp, Tier, tierOf, tierStyle, useRamp } from '../lib/grades'

type Metric = 'price' | 'change' | 'yield' | 'violent' | 'property'
type SortKey = 'tier' | 'name' | 'median12' | 'medianYoY' | 'change5y' | 'ppsf12' | 'sales12' | 'rent' | 'yield' | 'violentRate' | 'propertyRate'
/** Best available growth: the repeat-sales index where an area has enough pairs, else the change in median. */
const growth1 = (a: Area) => (a.priceGrowth1 !== undefined ? a.priceGrowth1 : a.medianYoY)
const growth5 = (a: Area) => (a.priceGrowth5 !== undefined ? a.priceGrowth5 : a.change5y)
const sortValue = (a: Area, k: Exclude<SortKey, 'tier'>) => (k === 'medianYoY' ? growth1(a) : k === 'change5y' ? growth5(a) : a[k])
const rateTip = (raw: number | null | undefined) => (raw == null ? undefined : `Adjusted for population size. Unadjusted: ${raw.toFixed(1)}`)
const CHANGE_BOUNDS = [-5, -1, 1, 5, Infinity]   // % bands for the 1-year change map
const CHANGE_LABELS = ['Down 5%+', 'Down 1–5%', 'Flat', 'Up 1–5%', 'Up 5%+']
/** Citywide price growth, by repeat sales and by change in median, so each area is set beside the same method. */
interface CityGrowth { repeat1: number | null; repeat5: number | null; median1: number | null; median5: number | null }
type AreasData = Areas & { cityGrowth?: CityGrowth }
/** Measures that get a grade, oriented so that A is what a buyer or investor would usually want. */
type Graded = 'medianYoY' | 'change5y' | 'sales12' | 'yield' | 'violentRate' | 'propertyRate'
const GRADED: { id: Graded; label: string; value: (a: Area) => number | null }[] = [
  { id: 'medianYoY', label: '1-yr change', value: growth1 },
  { id: 'change5y', label: '5-yr change', value: growth5 },
  { id: 'sales12', label: 'Homes sold', value: (a) => a.sales12 },
  { id: 'yield', label: 'Gross yield', value: (a) => a.yield },
  { id: 'violentRate', label: 'Violent incidents (fewer is higher)', value: (a) => (a.violentRate == null ? null : -a.violentRate) },
  { id: 'propertyRate', label: 'Property incidents (fewer is higher)', value: (a) => (a.propertyRate == null ? null : -a.propertyRate) },
]
interface Standing { pctl: Partial<Record<Graded, number>>; blend: number | null; tier: Tier | null }
const TIER_TIP = 'Tier: an even blend of price growth, yield, market activity and reported incidents, ranked within the city. Find areas lets you set your own weights.'

const METRICS: { id: Metric; label: string; title: string; ramp: string; value: (a: Area) => number | null; fmt: (v: number) => string }[] = [
  { id: 'price', label: 'Price', title: 'Median sale price', ramp: SCALE.price, value: (a) => a.median12, fmt: usdCompact },
  { id: 'change', label: '1-yr change', title: 'One-year price change', ramp: 'pchg', value: growth1, fmt: (v) => pct(v) },
  { id: 'yield', label: 'Rent yield', title: 'Gross rental yield', ramp: 'seq', value: (a) => a.yield, fmt: (v) => `${v.toFixed(1)}%` },
  { id: 'violent', label: 'Violent', title: 'Violent incidents per 1,000 residents', ramp: SCALE.incidents, value: (a) => a.violentRate, fmt: (v) => v.toFixed(1) },
  { id: 'property', label: 'Property', title: 'Property incidents per 1,000 residents', ramp: SCALE.incidents, value: (a) => a.propertyRate, fmt: (v) => v.toFixed(0) },
]

const LEGEND_ENDS: Partial<Record<Metric, [string, string]>> = {
  price: ['Lower', 'higher price'],
  violent: ['Fewer', 'more incidents per 1,000 residents'],
  property: ['Fewer', 'more incidents per 1,000 residents'],
}

/** The area to open, from the hash query: #/<city>/areas?area=<area key, URL-encoded> (an area name also works). */
const areaParam = () => new URLSearchParams(window.location.hash.split('?')[1] ?? '').get('area')

/** Keep ?area= in the hash in step with the open area, without a new history entry or a route change. */
function writeAreaParam(key: string | null) {
  const [route, query = ''] = window.location.hash.split('?')
  const params = new URLSearchParams(query)
  if (key) params.set('area', key)
  else params.delete('area')
  const qs = params.toString()
  window.history.replaceState(window.history.state, '', `${window.location.pathname}${window.location.search}${route}${qs ? `?${qs}` : ''}`)
}

export default function AreasPage({ city, theme }: { city: City; theme: Theme }) {
  const { data } = useData<AreasData>(`${city.id}/areas.json`)
  const [metric, setMetric] = useState<Metric>(() => METRICS.find((m) => m.id === new URLSearchParams(window.location.search).get('metric'))?.id ?? 'price')
  const [picked, setPickedState] = useState<string | null>(areaParam)
  const fromLink = useRef(picked != null)
  const setPicked = (key: string | null) => { setPickedState(key); writeAreaParam(key) }
  useEffect(() => {
    const on = () => setPickedState(areaParam())
    window.addEventListener('hashchange', on)
    return () => window.removeEventListener('hashchange', on)
  }, [])
  const [sort, setSort] = useState<{ key: SortKey; dir: 1 | -1 }>({ key: 'sales12', dir: -1 })
  const [query, setQuery] = useState('')
  // The map is made before areas.json arrives (its tiles are what a visitor waits for); the circles follow the data.
  // The table sits below the map: it is drawn just after the map and detail are on screen, in an interruptible render.
  const [tableOn, setTableOn] = useState(false)
  useEffect(() => {
    if (!data) return
    const t = setTimeout(() => startTransition(() => setTableOn(true)), 0)
    return () => clearTimeout(t)
  }, [data])

  const areas = data?.areas ?? []
  const M = METRICS.find((m) => m.id === metric)!
  const bounds = useMemo(() => metric === 'change' ? CHANGE_BOUNDS : quintiles(areas.map(M.value).filter((v): v is number => v != null)), [areas, metric])   // eslint-disable-line react-hooks/exhaustive-deps
  const maxSales = Math.max(1, ...areas.map((a) => a.sales12))
  const ramp = useRamp(theme)

  // percentile of each area within the city on each graded measure, then an even-weight blend and tier
  const standing = useMemo(() => {
    const out = new Map<string, Standing>()
    for (const a of areas) out.set(a.key, { pctl: {}, blend: null, tier: null })
    for (const g of GRADED) percentiles(areas, (a) => a.key, g.value).forEach((p, k) => { out.get(k)!.pctl[g.id] = p })
    // the tier uses "Active market" as on Find areas: mostly sales, a minor part from current listings
    const active = activityPercentiles(areas, (a) => a.key, (a) => a.sales12, [(a) => a.listings?.active ?? null, (a) => a.listings?.newListings ?? null])
    const blended: Standing[] = []
    out.forEach((s, key) => {
      const ps = (Object.entries(s.pctl) as [Graded, number][]).map(([id, p]) => (id === 'sales12' ? active.get(key) ?? p : p))
      if (ps.length * 2 < GRADED.length) return     // missing more than half the measures
      s.blend = blendOf(ps)
      blended.push(s)
    })
    blended.sort((x, y) => (y.blend as number) - (x.blend as number)).forEach((s, i) => { s.tier = tierOf(i, blended.length) })
    return out
  }, [areas])

  const points: MapPoint[] = useMemo(() => {
    const out: MapPoint[] = []
    for (const a of areas) {
      const v = M.value(a)
      if (a.lat == null || a.lon == null || v == null) continue
      out.push({
        id: a.key, lat: a.lat, lon: a.lon, radius: 5 + Math.sqrt(a.sales12 / maxSales) * 13,
        color: cssVar(`--${M.ramp}-${classOf(v, bounds) + 1}`),
        tip: `${a.name}${a.group ? ` (${a.group})` : ''}: ${M.id === 'price' ? '' : `${M.label.toLowerCase()} ${M.fmt(v)} · `}${usd(a.median12)} median, ${num(a.sales12)} sold`,
      })
    }
    return out.sort((a, b) => b.radius - a.radius)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [areas, metric, bounds, theme])

  const rows = useMemo(() => {
    const q = query.trim().toLowerCase()
    const list = areas.filter((a) => !q || `${a.name} ${a.group} ${(a.zips ?? []).join(' ')}`.toLowerCase().includes(q))
    return list.sort((a, b) => {
      const [x, y] = sort.key === 'tier' ? [standing.get(a.key)?.blend, standing.get(b.key)?.blend] : [sortValue(a, sort.key), sortValue(b, sort.key)]
      if (x == null) return 1
      if (y == null) return -1
      return (typeof x === 'string' ? x.localeCompare(y as string) : (x as number) - (y as number)) * sort.dir
    })
  }, [areas, sort, query, standing])

  const linked = picked == null ? null : areas.find((a) => a.key === picked) ?? areas.find((a) => a.name.toLowerCase() === picked.toLowerCase()) ?? null
  // opened from a link on a narrow screen: the detail sits below the map, so bring it into view once
  useEffect(() => {
    if (!linked || !fromLink.current || !tableOn) return   // once the table is in, so the page is its full length
    fromLink.current = false
    if (window.innerWidth < 900) document.querySelector('.a-detail')?.scrollIntoView({ block: 'start' })
  }, [linked, tableOn])

  const sel = linked ?? areas[0] ?? null   // open on the busiest area
  const onSort = (key: SortKey) => setSort((s) => ({ key, dir: s.key === key ? (s.dir === 1 ? -1 : 1) : key === 'name' ? 1 : -1 }))
  const noun = city.areaNoun.toLowerCase()
  const G = (a: Area, id: Graded) => <GradeMark ramp={ramp} p={standing.get(a.key)?.pctl[id]} label={GRADED.find((g) => g.id === id)!.label} />

  return (
    <>
      <div className="split">
        <section className="card a-map-card">
          <header className="card-head">
            <div><h3>{M.title} by {noun}</h3>
              <p>Bigger circle = more homes sold in 12 months. Tap one for details.</p></div>
            <Segmented label="Map colour" value={metric} onChange={setMetric} options={METRICS.map((m) => ({ id: m.id, label: m.label }))} />
          </header>
          <MapView center={city.center} zoom={city.zoom} points={points} theme={theme} selected={sel?.key ?? null} onSelect={(id) => { setPicked(String(id)); revealOnPhone(document.querySelector('.a-detail')) }} />
          {data && <div className="map-legend">
            {LEGEND_ENDS[metric] && <span className="muted">{LEGEND_ENDS[metric]![0]}</span>}
            {[0, 1, 2, 3, 4].map((i) => (
              <span key={i}><i className="key-box" style={{ background: `var(--${M.ramp}-${i + 1})` }} />
                {metric === 'change' ? CHANGE_LABELS[i] : `${i === 0 ? 'up to' : M.fmt(bounds[i - 1]) + ' –'} ${M.fmt(bounds[i])}`}</span>
            ))}
            {LEGEND_ENDS[metric] && <span className="muted">{LEGEND_ENDS[metric]![1]}</span>}
          </div>}
        </section>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 14, minWidth: 0 }}>
          {!data ? <div className="loading">Loading areas…</div> : sel ? <AreaDetail area={sel} data={data} city={city} ramp={ramp} standing={standing.get(sel.key)} onClose={() => setPicked(null)} /> : (
            <section className="card">
              <header className="card-head"><div><h3>Pick an area</h3><p>Select a circle on the map or a row in the table to see its price trend, sales volume and how it compares with the city.</p></div></header>
              <dl className="kv">
                <dt>Areas tracked</dt><dd>{areas.length}</dd>
                <dt>Highest median</dt><dd>{top(areas, 1)}</dd>
                <dt>Lowest median</dt><dd>{top(areas, -1)}</dd>
                <dt>City median, 12 months</dt><dd>{usd(city.kpi.median12)}</dd>
              </dl>
            </section>
          )}
        </div>
      </div>

      {data && <section className="card a-table-card">
        <header className="card-head">
          <div><h3>All areas</h3><p>Last 12 months. Areas with fewer than 12 sales are left out. Incidents are police-reported, per 1,000 residents. Tier blends growth, yield, sales and incidents evenly; <a href={`#/${city.id}/find`}>Find areas</a> lets you weight them.</p></div>
          <label className="field"><span>Find an area</span><input id="area-search" type="search" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Name or ZIP" /></label>
        </header>
        <div className="table-scroll" style={{ maxHeight: 560 }}>
          <table className="data">
            <thead><tr>
              <SortHead id="name" label={city.areaNoun} sort={sort} onSort={onSort} />
              <th className="a-tier-col" title={TIER_TIP} aria-sort={sort.key === 'tier' ? (sort.dir === 1 ? 'ascending' : 'descending') : 'none'}>
                <button className="sort" onClick={() => onSort('tier')}>Tier<span className="sort-mark">{sort.key === 'tier' ? (sort.dir === 1 ? '↑' : '↓') : ''}</span></button>
              </th>
              <SortHead id="median12" label="Median price" sort={sort} onSort={onSort} num />
              <SortHead id="medianYoY" label="1-yr growth" sort={sort} onSort={onSort} num />
              <SortHead id="change5y" label="5-yr growth" sort={sort} onSort={onSort} num />
              <SortHead id="ppsf12" label="$/sq ft" sort={sort} onSort={onSort} num />
              <SortHead id="sales12" label="Homes sold" sort={sort} onSort={onSort} num />
              <SortHead id="rent" label="Rent / mo" sort={sort} onSort={onSort} num />
              <SortHead id="yield" label="Rent yield" sort={sort} onSort={onSort} num />
              <SortHead id="violentRate" label="Violent / 1k" sort={sort} onSort={onSort} num />
              <SortHead id="propertyRate" label="Property / 1k" sort={sort} onSort={onSort} num />
              <th>Trend</th>
            </tr></thead>
            <tbody>
              {tableOn && rows.map((a) => (
                <tr key={a.key} className={`click ${a.key === sel?.key ? 'sel' : ''}`} onClick={() => { setPicked(a.key); revealOnPhone(document.querySelector('.a-detail')) }}>
                  <td><b>{a.name}</b>{a.group && <span className="muted"> {a.group}</span>}{(a.zips ?? []).length > 0 && <span className="muted a-zip">{(a.zips ?? []).join(', ')}</span>}</td>
                  <td className="a-tier-col"><TierMark ramp={ramp} tier={standing.get(a.key)?.tier ?? null} /></td>
                  <td className="num">{usd(a.median12)}</td>
                  <td className="num" title={growthTip(a)}>{pct(growth1(a))}{G(a, 'medianYoY')}</td>
                  <td className="num" title={growthTip(a)}>{pct(growth5(a), 0)}{G(a, 'change5y')}</td>
                  <td className="num">{a.ppsf12 == null ? '–' : usd(a.ppsf12)}</td>
                  <td className="num">{num(a.sales12)}{G(a, 'sales12')}</td>
                  <td className="num" title={a.rent == null ? undefined : rentLines(a).join('\n')}>{a.rent == null ? '–' : usd(a.rent)}</td>
                  <td className="num" title={yieldRange(a) ?? undefined}>{a.yield == null ? '–' : <>{a.yield.toFixed(1)}%{G(a, 'yield')}</>}</td>
                  <td className="num" title={rateTip(a.violentRaw)}>{a.violentRate == null ? '–' : <>{a.violentRate.toFixed(1)}{G(a, 'violentRate')}</>}</td>
                  <td className="num" title={rateTip(a.propertyRaw)}>{a.propertyRate == null ? '–' : <>{a.propertyRate.toFixed(1)}{G(a, 'propertyRate')}</>}</td>
                  <td><Sparkline values={a.quarterly} /></td>
                </tr>
              ))}
              {tableOn && rows.length === 0 && <tr><td colSpan={12} className="muted">No area matches “{query}”.</td></tr>}
            </tbody>
          </table>
        </div>
        <p className="a-legend">
          <GradeMark ramp={ramp} p={90} label="" /> A = top fifth of {city.name} areas on that measure, <GradeMark ramp={ramp} p={0} label="" /> E = bottom fifth. Fewer reported incidents grade higher.
        </p>
      </section>}
    </>
  )
}

function growthTip(a: Area): string | undefined {
  if (a.growthMethod === 'repeat') return 'From homes here that sold twice, so the mix of homes does not skew it'
  if (a.growthMethod === 'median') return 'Change in the median sale price'
  return undefined
}

/** The city's growth by the same method as the area's, falling back to the other method. */
function cityGrowthFor(a: Area, cg: CityGrowth | undefined, years: 1 | 5): number | null {
  if (!cg) return null
  const [first, second] = a.growthMethod === 'repeat' ? ['repeat', 'median'] : ['median', 'repeat']
  return cg[`${first}${years}` as keyof CityGrowth] ?? cg[`${second}${years}` as keyof CityGrowth] ?? null
}

/** Which of the two city growth figures an area is shown against, in words for the hover label. */
function cityGrowthLabel(a: Area, cg: CityGrowth | undefined, years: 1 | 5): string {
  if (!cg) return ''
  const own = a.growthMethod === 'repeat' ? 'repeat' : 'median'
  const used = cg[`${own}${years}` as keyof CityGrowth] != null ? own : own === 'repeat' ? 'median' : 'repeat'
  return used === 'repeat' ? ' (resold homes)' : ' (median price)'
}

/** Why the city growth figure differs between neighbourhoods: shown when a growth row is opened. */
function growthWhy(a: Area, cg: CityGrowth | undefined, years: 1 | 5, digits: number): string {
  const repeat = a.growthMethod === 'repeat'
  const other = cg?.[`${repeat ? 'median' : 'repeat'}${years}` as keyof CityGrowth]
  const mine = repeat ? 'Measured from homes here that sold twice, so a change in which homes sell does not skew it.'
    : 'Too few homes here sold twice, so this is the change in the median sale price.'
  return `${mine}\nThe city figure is measured the same way, to compare like with like.` +
    (other == null ? '' : ` By ${repeat ? 'median price' : 'resold homes'} the city is ${pct(other, digits)}, which is why other neighborhoods can show a different city number.`)
}

/** Year-on-year change in all reported incidents, in plain words. */
function trendWord(t: { crimeTrend?: string | null; crimeYoY: number | null }): string {
  if (t.crimeTrend === 'flat' || t.crimeYoY == null) return 'no clear change'
  return `${t.crimeYoY > 0 ? 'up' : 'down'} ${Math.abs(t.crimeYoY).toFixed(0)}%`
}

const YIELD_NOTE = 'Yield is a year of rent divided by the median price. “After typical costs” assumes about half the rent goes on property tax, insurance, repairs, empty months and management; mortgage costs are not included.'

function top(areas: Area[], dir: 1 | -1): string {
  const ranked = areas.filter((a) => a.median12 != null).sort((a, b) => ((b.median12 as number) - (a.median12 as number)) * dir)
  return ranked.length ? `${ranked[0].name} · ${usd(ranked[0].median12)}` : '–'
}

function GradeMark({ p, ramp, label }: { p: number | null | undefined; ramp: Ramp; label: string }) {
  const g = gradeOf(p)
  if (g == null) return null
  return <span className={`g-sq sm ${g === 'E' ? 'e' : ''}`} style={gradeStyle(g, ramp)} title={label ? `${label}: higher than ${p}% of areas in the city` : undefined}>{g}</span>
}

function TierMark({ tier, ramp }: { tier: Tier | null; ramp: Ramp }) {
  if (tier == null) return <span className="f-nodata" title="Too little data for a tier">–</span>
  return <span className="f-tier" style={tierStyle(tier, ramp)} title={TIER_TIP}>{tier}</span>
}

/** Rows that are plain figures; the rest are graded or coloured against the city and sit in their own group below. */
const PLAIN_ROWS = ['price', 'ppsf', 'rent', 'sold']

interface DetailRow {
  id: string; label: string; grade?: Graded; value: ReactNode
  more?: string                                                    // exact figures, shown on hover and on tap
  bar?: { v: number | null; c: number | null; lo: number; hi: number; tone?: 'good' | 'bad' }
  tip?: [string, string | null]                                    // [this area, city], shown when the bar is hovered
  better?: 'up' | 'down'                                           // which way is better, where the site grades it; colours the hover figure
  bar2?: DetailRow['bar']; tip2?: [string, string | null]          // a second, quieter bar under the first
}

/** Scale ends for each bar, from the spread across the city's areas, so one area's bars read the same way as another's. */
function useDomains(areas: Area[]) {
  return useMemo(() => {
    const q95 = (f: (a: Area) => number | null | undefined, abs = false) => {
      const v = areas.map(f).filter((x): x is number => x != null).map((x) => (abs ? Math.abs(x) : x)).sort((a, b) => a - b)
      return v.length ? v[Math.floor(0.95 * (v.length - 1))] : 1
    }
    return {
      price: q95((a) => a.median12), g1: q95(growth1, true), g5: q95(growth5, true), rent: q95((a) => a.rent), yield: q95((a) => a.yield), ppsf: q95((a) => a.ppsf12),
      violent: q95((a) => a.violentRate), property: q95((a) => a.propertyRate), crime: Math.max(20, q95((a) => a.crimeYoY, true)),
    }
  }, [areas])
}

function AreaDetail({ area, data, city, ramp, standing, onClose }: { area: Area; data: AreasData; city: City; ramp: Ramp; standing?: Standing; onClose: () => void }) {
  const [open, setOpen] = useState<string | null>(null)
  const dom = useDomains(data.areas)
  const cx = city.context
  const level = (v: number | null | undefined, c: number | null | undefined, top: number) =>
    ({ v: v ?? null, c: c ?? null, lo: 0, hi: Math.max(top, v ?? 0, c ?? 0) * 1.05 })
  const change = (v: number | null | undefined, c: number | null | undefined, top: number, goodWhenUp: boolean) => {
    const m = Math.max(top, Math.abs(v ?? 0), Math.abs(c ?? 0)) * 1.1
    const tone: 'good' | 'bad' | undefined = v == null || v === 0 ? undefined : (v > 0) === goodWhenUp ? 'good' : 'bad'
    return { v: v ?? null, c: c ?? null, lo: -m, hi: m, tone }
  }
  const vs = (c: string | null) => (c == null ? '' : ` · city ${c}`)

  const g1 = growth1(area), g5 = growth5(area)
  const c1 = cityGrowthFor(area, data.cityGrowth, 1), c5 = cityGrowthFor(area, data.cityGrowth, 5)
  const gap = area.median12 != null && city.kpi.median12 ? (area.median12 / city.kpi.median12 - 1) * 100 : null
  const crimeV = area.crimeTrend === 'flat' ? 0 : area.crimeYoY
  const crimeC = cx.crimeTrend === 'flat' ? 0 : cx.crimeYoY
  const cityPpsf = city.kpi.ppsf12 ?? city.kpi.ppsf
  const netShare = area.yield && area.netYield != null ? area.netYield / area.yield : null   // the engine's flat costs share
  const cxYield = cx.yield == null ? null : `${cx.yield.toFixed(1)}%`
  const tipOf = (t: [string, string | null] | undefined, better?: 'up' | 'down'): Tip | undefined => t && { area: t[0], city: t[1], better }
  const rows: DetailRow[] = [
    { id: 'price', label: 'Median price', value: usd(area.median12), tip: [usdCompact(area.median12), usdCompact(city.kpi.median12)], bar: level(area.median12, city.kpi.median12, dom.price),
      more: `${usd(area.median12)}${vs(usd(city.kpi.median12))}${gap == null ? '' : Math.abs(gap) < 0.5 ? ' (about the same)' : ` (${Math.abs(gap).toFixed(0)}% ${gap > 0 ? 'above' : 'below'})`}` +
        (area.medianLow != null && area.medianHigh != null ? `. Likely range ${usdCompact(area.medianLow)}–${usdCompact(area.medianHigh)}.` : '') },
    { id: 'g1', label: 'Growth, 1 yr', grade: 'medianYoY', value: g1 == null ? 'too few sales' : pct(g1), tip: [pct(g1), c1 == null ? null : pct(c1) + cityGrowthLabel(area, data.cityGrowth, 1)], better: 'up', bar: change(g1, c1, dom.g1, true),
      more: g1 == null ? undefined : `${pct(g1)}${vs(c1 == null ? null : pct(c1))}\n${growthWhy(area, data.cityGrowth, 1, 1)}` },
    { id: 'g5', label: 'Growth, 5 yrs', grade: 'change5y', value: g5 == null ? 'too few sales' : pct(g5, 0), tip: [pct(g5, 0), c5 == null ? null : pct(c5, 0) + cityGrowthLabel(area, data.cityGrowth, 5)], better: 'up', bar: change(g5, c5, dom.g5, true),
      more: g5 == null ? undefined : `${pct(g5, 0)}${vs(c5 == null ? null : pct(c5, 0))}\n${growthWhy(area, data.cityGrowth, 5, 0)}` },
    { id: 'ppsf', label: 'Price / sq ft', value: area.ppsf12 == null ? 'not published' : usd(area.ppsf12), tip: [usd(area.ppsf12), cityPpsf == null ? null : usd(cityPpsf)],
      bar: area.ppsf12 == null ? undefined : level(area.ppsf12, cityPpsf, dom.ppsf) },
    { id: 'sold', label: 'Homes sold', grade: 'sales12', value: num(area.sales12),
      more: `${num(area.sales12)} in 12 months${area.salesYoY != null ? `, ${pct(area.salesYoY, 0)} on the year before` : ''}` },
    { id: 'rent', label: 'Rent', value: rentValue(area), tip: [usd(area.rent), cx.rent == null ? null : usd(cx.rent)], bar: area.rent == null ? undefined : level(area.rent, cx.rent, dom.rent),
      more: area.rent == null ? undefined : rentLines(area, cx).join('\n') },
    { id: 'yield', label: 'Rent yield', grade: 'yield', value: area.yield == null ? '–' : area.netYield == null ? `${area.yield.toFixed(1)}%`
        : <span className="a-two"><span>{area.netYield.toFixed(1)}% net</span><span className="a-faint">{area.yield.toFixed(1)}% gross</span></span>,
      better: 'up', ...(area.yield == null ? {} : area.netYield == null || netShare == null
        ? { tip: [`${area.yield.toFixed(1)}%`, cxYield] as [string, string | null], bar: level(area.yield, cx.yield, dom.yield) }
        : { tip: [`${area.netYield.toFixed(1)}% net`, cx.yield == null ? null : `${(cx.yield * netShare).toFixed(1)}%`] as [string, string | null],
            bar: level(area.netYield, cx.yield == null ? null : cx.yield * netShare, dom.yield * netShare),
            tip2: [`${area.yield.toFixed(1)}% gross`, cxYield] as [string, string | null], bar2: level(area.yield, cx.yield, dom.yield) }),
      more: area.yield == null ? undefined : `${area.yield.toFixed(1)}% before costs${area.netYield != null ? `, about ${area.netYield.toFixed(1)}% after typical costs` : ''}${vs(cx.yield == null ? null : `${cx.yield.toFixed(1)}%`)}.${yieldRange(area) ? `\nRange ${yieldRange(area)}.` : ''}\n${YIELD_NOTE}` },
  ]
  if (area.violentRate == null) rows.push({ id: 'inc', label: 'Incidents', value: 'too few residents' })
  else rows.push(
    { id: 'violent', label: 'Violent / 1,000', grade: 'violentRate', value: area.violentRate.toFixed(1), tip: [area.violentRate.toFixed(1), cx.violentRate?.toFixed(1) ?? null], better: 'down', bar: level(area.violentRate, cx.violentRate, dom.violent),
      more: `${area.violentRate.toFixed(1)} per 1,000${vs(cx.violentRate?.toFixed(1) ?? null)}${area.violentRaw != null ? `. Unadjusted ${area.violentRaw.toFixed(1)}.` : ''}` },
    { id: 'property', label: 'Property / 1,000', grade: 'propertyRate', value: area.propertyRate?.toFixed(1) ?? '–', tip: [area.propertyRate?.toFixed(1) ?? '–', cx.propertyRate?.toFixed(1) ?? null], better: 'down', bar: level(area.propertyRate, cx.propertyRate, dom.property),
      more: `${area.propertyRate?.toFixed(1)} per 1,000${vs(cx.propertyRate?.toFixed(1) ?? null)}${area.propertyRaw != null ? `. Unadjusted ${area.propertyRaw.toFixed(1)}.` : ''}` },
    { id: 'crime', label: 'Incidents, 1 yr', value: trendWord(area), tip: [trendWord(area), cx.crimeYoY != null || cx.crimeTrend === 'flat' ? trendWord(cx) : null], better: 'down', bar: change(crimeV, crimeC, dom.crime, false),
      more: `${trendWord(area)}${vs(cx.crimeYoY != null || cx.crimeTrend === 'flat' ? trendWord(cx) : null)}` },
  )

  const renderRow = (r: DetailRow) => {
    const inner = <>
      <span className="a-label">{r.label}{r.grade && <GradeMark ramp={ramp} p={standing?.pctl[r.grade]} label={GRADED.find((g) => g.id === r.grade)!.label} />}</span>
      <span className="a-val">{r.value}</span>
      {!r.bar ? <span className="a-bar none" /> : !r.bar2 ? <MiniBar {...r.bar} tip={tipOf(r.tip, r.better)} />
        : <span className="a-bars"><MiniBar {...r.bar} tip={tipOf(r.tip, r.better)} /><MiniBar {...r.bar2} tip={tipOf(r.tip2, r.better)} quiet /></span>}
    </>
    if (!r.more) return <div key={r.id} className="a-row">{inner}</div>
    const on = open === r.id
    return (
      <div key={r.id} className="a-row-wrap">
        <button className={`a-row ${on ? 'on' : ''}`} title={r.more} aria-expanded={on} onClick={() => setOpen(on ? null : r.id)}>{inner}</button>
        {on && <p className="a-more">{r.more}</p>}
      </div>
    )
  }

  return (
    <section className="card a-detail">
      <header className="card-head">
        <div><h3>{area.name}{area.group ? ` · ${area.group}` : ''}</h3>
          <p>{(area.zips ?? []).length > 0 && <>ZIP {(area.zips ?? []).join(', ')} · </>}{area.topTypeShare}% {area.topType.toLowerCase()} homes</p></div>
        <div className="a-tier-head"><TierMark tier={standing?.tier ?? null} ramp={ramp} /><button className="ghost small" onClick={onClose}>Close</button></div>
      </header>
      <div className="a-rows">
        {[...rows.filter((r) => PLAIN_ROWS.includes(r.id)), ...rows.filter((r) => !PLAIN_ROWS.includes(r.id))].map((r, i) => {
          const head = i === 0 ? 'Prices and sales' : i === PLAIN_ROWS.length ? 'Compared with the city' : null
          const row = renderRow(r)
          return head ? <Fragment key={r.id}><div className="a-group">{head}</div>{row}</Fragment> : row
        })}
      </div>
      {area.listings && <ListingLine l={area.listings} />}
      <LineChart labels={data.quarters} series={[{ name: 'Median sale price', color: 'var(--series-1)', values: area.quarterly }]}
        ticks={() => data.quarters.map((q, index) => ({ index, label: q.slice(0, 4), first: q.endsWith('Q1') })).filter((t) => t.first)}
        format={usd} axisFormat={usdCompact} height={230} />
      <p className="footnote" style={{ marginTop: 8 }}>Quarterly median; quarters with under five sales are blank. <a href={`#/${city.id}/sales`}>Recent sales</a></p>
    </section>
  )
}

/** Current listing figures, kept to one plain line next to the recorded-sales figures above. */
function ListingLine({ l }: { l: Listings }) {
  const parts = [
    l.active != null && `${num(l.active)} for sale`,
    l.newListings != null && `${num(l.newListings)} newly listed`,
    l.priceCutShare != null && `${l.priceCutShare.toFixed(0)}% with a price cut`,
  ].filter(Boolean)
  if (!parts.length) return null
  return <p className="f-listings" title="Current listings from Zillow Research, by ZIP code. Not recorded sales.">Listings, {monthLabel(l.month)}: {parts.join(' · ')}</p>
}
