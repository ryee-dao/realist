import '../styles/finder.css'
import '../styles/areas.css'
import { ReactNode, useEffect, useMemo, useRef, useState } from 'react'
import { Area, Areas, City, Listings, useData } from '../lib/data'
import { Theme, cssVar } from '../lib/hooks'
import { monthLabel, num, pct, usd, usdCompact } from '../lib/format'
import MapView, { classOf, MapPoint, quintiles, SCALE } from '../components/MapView'
import LineChart from '../charts/LineChart'
import Sparkline from '../charts/Sparkline'
import { Segmented, SortHead } from '../components/bits'
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
        tip: `${a.name}${a.group ? ` (${a.group})` : ''}: ${M.title.toLowerCase()} ${M.fmt(v)} · median ${usd(a.median12)}, ${num(a.sales12)} sold`,
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
    if (!linked || !fromLink.current) return
    fromLink.current = false
    if (window.innerWidth < 900) document.querySelector('.a-detail')?.scrollIntoView({ block: 'start' })
  }, [linked])

  if (!data) return <div className="loading">Loading areas…</div>
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
          <MapView center={city.center} zoom={city.zoom} points={points} theme={theme} selected={sel?.key ?? null} onSelect={(id) => setPicked(String(id))} />
          <div className="map-legend">
            {LEGEND_ENDS[metric] && <span className="muted">{LEGEND_ENDS[metric]![0]}</span>}
            {[0, 1, 2, 3, 4].map((i) => (
              <span key={i}><i className="key-box" style={{ background: `var(--${M.ramp}-${i + 1})` }} />
                {metric === 'change' ? CHANGE_LABELS[i] : `${i === 0 ? 'up to' : M.fmt(bounds[i - 1]) + ' –'} ${M.fmt(bounds[i])}`}</span>
            ))}
            {LEGEND_ENDS[metric] && <span className="muted">{LEGEND_ENDS[metric]![1]}</span>}
          </div>
        </section>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 14, minWidth: 0 }}>
          {sel ? <AreaDetail area={sel} data={data} city={city} ramp={ramp} standing={standing.get(sel.key)} onClose={() => setPicked(null)} /> : (
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

      <section className="card">
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
              <SortHead id="medianYoY" label="1-yr change" sort={sort} onSort={onSort} num />
              <SortHead id="change5y" label="5-yr change" sort={sort} onSort={onSort} num />
              <SortHead id="ppsf12" label="Per sq ft" sort={sort} onSort={onSort} num />
              <SortHead id="sales12" label="Homes sold" sort={sort} onSort={onSort} num />
              <SortHead id="rent" label="Rent / mo" sort={sort} onSort={onSort} num />
              <SortHead id="yield" label="Gross yield" sort={sort} onSort={onSort} num />
              <SortHead id="violentRate" label="Violent / 1k" sort={sort} onSort={onSort} num />
              <SortHead id="propertyRate" label="Property / 1k" sort={sort} onSort={onSort} num />
              <th>Quarterly trend</th>
            </tr></thead>
            <tbody>
              {rows.map((a) => (
                <tr key={a.key} className={`click ${a.key === sel?.key ? 'sel' : ''}`} onClick={() => setPicked(a.key)}>
                  <td><b>{a.name}</b>{a.group && <span className="muted"> {a.group}</span>}{(a.zips ?? []).length > 0 && <span className="muted"> {(a.zips ?? []).join(', ')}</span>}</td>
                  <td className="a-tier-col"><TierMark ramp={ramp} tier={standing.get(a.key)?.tier ?? null} /></td>
                  <td className="num">{usd(a.median12)}</td>
                  <td className="num" title={growthTip(a)}>{pct(growth1(a))}{G(a, 'medianYoY')}</td>
                  <td className="num" title={growthTip(a)}>{pct(growth5(a), 0)}{G(a, 'change5y')}</td>
                  <td className="num">{a.ppsf12 == null ? '–' : usd(a.ppsf12)}</td>
                  <td className="num">{num(a.sales12)}{G(a, 'sales12')}</td>
                  <td className="num">{a.rent == null ? '–' : usd(a.rent)}</td>
                  <td className="num">{a.yield == null ? '–' : <>{a.yield.toFixed(1)}%{G(a, 'yield')}</>}</td>
                  <td className="num" title={rateTip(a.violentRaw)}>{a.violentRate == null ? '–' : <>{a.violentRate.toFixed(1)}{G(a, 'violentRate')}</>}</td>
                  <td className="num" title={rateTip(a.propertyRaw)}>{a.propertyRate == null ? '–' : <>{a.propertyRate.toFixed(1)}{G(a, 'propertyRate')}</>}</td>
                  <td><Sparkline values={a.quarterly} /></td>
                </tr>
              ))}
              {rows.length === 0 && <tr><td colSpan={12} className="muted">No area matches “{query}”.</td></tr>}
            </tbody>
          </table>
        </div>
        <p className="a-legend">
          <GradeMark ramp={ramp} p={90} label="" /> A = top fifth of {city.name} areas on that measure, <GradeMark ramp={ramp} p={0} label="" /> E = bottom fifth. Fewer reported incidents grade higher.
        </p>
      </section>
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

/** Year-on-year change in all reported incidents, in plain words. */
function trendWord(t: { crimeTrend?: string | null; crimeYoY: number | null }): string {
  if (t.crimeTrend === 'flat' || t.crimeYoY == null) return 'no clear change'
  return `${t.crimeYoY > 0 ? 'up' : 'down'} ${Math.abs(t.crimeYoY).toFixed(0)}%`
}

const YIELD_NOTE = 'A year of typical rent divided by the median price. “After typical costs” assumes about half the rent goes on property tax, insurance, repairs, empty months and management. Mortgage costs are not included.'

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

function AreaDetail({ area, data, city, ramp, standing, onClose }: { area: Area; data: AreasData; city: City; ramp: Ramp; standing?: Standing; onClose: () => void }) {
  const G = (id: Graded): ReactNode => <GradeMark ramp={ramp} p={standing?.pctl[id]} label={GRADED.find((g) => g.id === id)!.label} />
  const [note, setNote] = useState(false)
  const gap = area.median12 != null && city.kpi.median12 ? (area.median12 / city.kpi.median12 - 1) * 100 : null
  const cx = city.context
  const vsCity = (v: string) => <span className="muted"> · city {v}</span>
  const growth = (years: 1 | 5) => {
    const v = years === 1 ? growth1(area) : growth5(area)
    if (v == null) return 'too few sales'
    const c = cityGrowthFor(area, data.cityGrowth, years)
    return <span title={growthTip(area)}>{pct(v, years === 1 ? 1 : 0)}{c != null && vsCity(pct(c, years === 1 ? 1 : 0))}</span>
  }
  return (
    <section className="card a-detail">
      <header className="card-head">
        <div><h3>{area.name}{area.group ? ` · ${area.group}` : ''}</h3>
          <p>{area.topTypeShare}% of recent sales are {area.topType.toLowerCase()} homes.</p></div>
        <div className="a-tier-head"><TierMark tier={standing?.tier ?? null} ramp={ramp} /><button className="ghost small" onClick={onClose}>Close</button></div>
      </header>
      <dl className="kv a-kv">
        <dt>ZIP codes</dt><dd>{(area.zips ?? []).length ? (area.zips ?? []).join(', ') : '–'}</dd>
        <dt>Median price</dt><dd title={area.medianLow != null && area.medianHigh != null ? `Likely range ${usdCompact(area.medianLow)}–${usdCompact(area.medianHigh)}` : undefined}>
          {usd(area.median12)}{gap != null && <span className="muted"> · {Math.abs(gap) < 0.5 ? 'same as city' : `${Math.abs(gap).toFixed(0)}% ${gap > 0 ? 'above' : 'below'} city`}</span>}</dd>
        <dt>Growth, 1 yr</dt><dd>{growth(1)}{G('medianYoY')}</dd>
        <dt>Growth, 5 yrs</dt><dd>{growth(5)}{G('change5y')}</dd>
        <dt>Price / sq ft</dt><dd>{area.ppsf12 == null ? 'not published' : usd(area.ppsf12)}</dd>
        <dt>Homes sold</dt><dd>{num(area.sales12)}{area.salesYoY != null && <span className="muted"> · {pct(area.salesYoY, 0)} in a year</span>}{G('sales12')}</dd>
        <dt>Typical rent</dt><dd>{area.rent == null ? 'no estimate' : <>{usd(area.rent)}/mo{area.rentYoY != null && <span className="muted"> · {pct(area.rentYoY)} in a year</span>}</>}</dd>
        <dt>Rent yield</dt>
        <dd>{area.yield == null ? '–' : <>
          {area.yield.toFixed(1)}% before costs{G('yield')}
          {area.netYield != null && <><br /><span className="muted">about {area.netYield.toFixed(1)}% after typical costs</span></>}
          <button className="a-info" aria-expanded={note} aria-label="What costs?" title={YIELD_NOTE} onClick={() => setNote(!note)}>?</button>
          {note && <span className="a-note">{YIELD_NOTE}</span>}
        </>}</dd>
        {area.violentRate == null ? <><dt>Incidents</dt><dd className="muted">too few residents for a rate</dd></> : <>
          <dt>Violent</dt><dd title={area.violentRaw != null ? `Unadjusted: ${area.violentRaw.toFixed(1)}` : undefined}>{area.violentRate.toFixed(1)} per 1,000{cx.violentRate != null && vsCity(cx.violentRate.toFixed(1))}{G('violentRate')}</dd>
          <dt>Property</dt><dd title={area.propertyRaw != null ? `Unadjusted: ${area.propertyRaw.toFixed(1)}` : undefined}>{area.propertyRate?.toFixed(1)} per 1,000{cx.propertyRate != null && vsCity(cx.propertyRate.toFixed(1))}{G('propertyRate')}</dd>
          <dt>Incidents, 1 yr</dt><dd>{trendWord(area)}{(cx.crimeYoY != null || cx.crimeTrend === 'flat') && vsCity(trendWord(cx))}</dd>
        </>}
      </dl>
      <p className="a-kv-note">Incidents are police reports per 1,000 residents.</p>
      {area.listings && <ListingLine l={area.listings} />}
      <LineChart labels={data.quarters} series={[{ name: 'Median sale price', color: 'var(--series-1)', values: area.quarterly }]}
        ticks={() => data.quarters.map((q, index) => ({ index, label: q.slice(0, 4), first: q.endsWith('Q1') })).filter((t) => t.first)}
        format={usd} axisFormat={usdCompact} height={230} />
      <p className="footnote" style={{ marginTop: 8 }}>Quarterly median; quarters with under five sales are blank. <a href={`#/${city.id}/sold`}>Sold prices</a></p>
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
