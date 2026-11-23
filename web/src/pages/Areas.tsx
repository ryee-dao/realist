import '../styles/finder.css'
import { ReactNode, useMemo, useState } from 'react'
import { Area, Areas, City, useData } from '../lib/data'
import { Theme, cssVar } from '../lib/hooks'
import { num, pct, usd, usdCompact } from '../lib/format'
import MapView, { classOf, MapPoint, quintiles, SCALE } from '../components/MapView'
import LineChart from '../charts/LineChart'
import Sparkline from '../charts/Sparkline'
import { Segmented, SortHead } from '../components/bits'
import MarketNow from '../components/MarketNow'
import { blendOf, gradeOf, gradeStyle, percentiles, Ramp, Tier, tierOf, tierStyle, useRamp } from '../lib/grades'

type Metric = 'price' | 'change' | 'yield' | 'violent' | 'property'
type SortKey = 'tier' | 'name' | 'median12' | 'medianYoY' | 'change5y' | 'ppsf12' | 'sales12' | 'rent' | 'yield' | 'violentRate' | 'propertyRate'
/** Best available growth: the repeat-sales index where an area has enough pairs, else the change in median. */
const growth1 = (a: Area) => (a.priceGrowth1 !== undefined ? a.priceGrowth1 : a.medianYoY)
const growth5 = (a: Area) => (a.priceGrowth5 !== undefined ? a.priceGrowth5 : a.change5y)
const sortValue = (a: Area, k: Exclude<SortKey, 'tier'>) => (k === 'medianYoY' ? growth1(a) : k === 'change5y' ? growth5(a) : a[k])
const rateTip = (raw: number | null | undefined) => (raw == null ? undefined : `Adjusted for population size. Unadjusted: ${raw.toFixed(1)}`)
const CHANGE_BOUNDS = [-5, -1, 1, 5, Infinity]   // % bands for the 1-year change map
const CHANGE_LABELS = ['Down 5%+', 'Down 1–5%', 'Flat', 'Up 1–5%', 'Up 5%+']
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
const TIER_TIP = 'Tier: an even blend of price growth, yield, homes sold and reported incidents, ranked within the city. Find areas lets you set your own weights.'

const METRICS: { id: Metric; label: string; title: string; ramp: string; value: (a: Area) => number | null; fmt: (v: number) => string }[] = [
  { id: 'price', label: 'Price', title: 'Median sale price', ramp: SCALE.price, value: (a) => a.median12, fmt: usdCompact },
  { id: 'change', label: '1-yr change', title: 'One-year price change', ramp: 'div', value: growth1, fmt: (v) => pct(v) },
  { id: 'yield', label: 'Rent yield', title: 'Gross rental yield', ramp: 'seq', value: (a) => a.yield, fmt: (v) => `${v.toFixed(1)}%` },
  { id: 'violent', label: 'Violent', title: 'Violent incidents per 1,000 residents', ramp: SCALE.incidents, value: (a) => a.violentRate, fmt: (v) => v.toFixed(1) },
  { id: 'property', label: 'Property', title: 'Property incidents per 1,000 residents', ramp: SCALE.incidents, value: (a) => a.propertyRate, fmt: (v) => v.toFixed(0) },
]

const LEGEND_ENDS: Partial<Record<Metric, [string, string]>> = {
  price: ['Lower', 'higher price'],
  violent: ['Fewer', 'more incidents per 1,000 residents'],
  property: ['Fewer', 'more incidents per 1,000 residents'],
}

export default function AreasPage({ city, theme }: { city: City; theme: Theme }) {
  const { data } = useData<Areas>(`${city.id}/areas.json`)
  const [metric, setMetric] = useState<Metric>(() => METRICS.find((m) => m.id === new URLSearchParams(window.location.search).get('metric'))?.id ?? 'price')
  const [picked, setPicked] = useState<string | null>(null)
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
    const blended: Standing[] = []
    out.forEach((s) => {
      const ps = Object.values(s.pctl) as number[]
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

  if (!data) return <div className="loading">Loading areas…</div>
  const sel = areas.find((a) => a.key === picked) ?? areas[0] ?? null   // open on the busiest area
  const onSort = (key: SortKey) => setSort((s) => ({ key, dir: s.key === key ? (s.dir === 1 ? -1 : 1) : key === 'name' ? 1 : -1 }))
  const noun = city.areaNoun.toLowerCase()
  const G = (a: Area, id: Graded) => <GradeMark ramp={ramp} p={standing.get(a.key)?.pctl[id]} label={GRADED.find((g) => g.id === id)!.label} />

  return (
    <>
      <div className="split">
        <section className="card">
          <header className="card-head">
            <div><h3>{M.title} by {noun}</h3>
              <p>Circle size shows homes sold in the last 12 months. Click a circle for its trend.</p></div>
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
          <div><h3>All areas</h3><p>Last 12 months. Incident rates are police-reported, per 1,000 residents of the area's catchment. Areas with fewer than 12 sales are left out. Tier blends growth, yield, sales and reported incidents evenly; <a href={`#/${city.id}/find`}>Find areas</a> lets you weight them.</p>
              <p>Growth comes from repeat sales of the same homes where there are enough, otherwise the change in median. Incident rates are adjusted toward the city rate where few people live.</p></div>
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
  if (a.growthMethod === 'repeat') return `Repeat sales: ${num(a.repeatPairs)} homes sold twice`
  if (a.growthMethod === 'median') return 'Change in median sale price'
  return undefined
}

const method = (a: Area) => (a.growthMethod === 'repeat' ? `repeat sales, ${num(a.repeatPairs)} pairs` : 'change in median')

function trendWord(a: Area): string {
  if (a.crimeTrend === 'flat') return 'no clear change in a year'
  if ((a.crimeTrend === 'up' || a.crimeTrend === 'down') && a.crimeYoY != null) return `${a.crimeTrend} ${Math.abs(a.crimeYoY).toFixed(0)}% in a year`
  return a.crimeYoY == null ? 'no clear change in a year' : `${pct(a.crimeYoY)} in a year`
}

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

function AreaDetail({ area, data, city, ramp, standing, onClose }: { area: Area; data: Areas; city: City; ramp: Ramp; standing?: Standing; onClose: () => void }) {
  const G = (id: Graded): ReactNode => <GradeMark ramp={ramp} p={standing?.pctl[id]} label={GRADED.find((g) => g.id === id)!.label} />
  const gap = area.median12 != null && city.kpi.median12 ? (area.median12 / city.kpi.median12 - 1) * 100 : null
  return (
    <section className="card">
      <header className="card-head">
        <div><h3>{area.name}{area.group ? ` · ${area.group}` : ''}</h3>
          <p>{area.topTypeShare}% of recent sales here are {area.topType.toLowerCase()} homes.</p></div>
        <div className="a-tier-head"><TierMark tier={standing?.tier ?? null} ramp={ramp} /><button className="ghost small" onClick={onClose}>Close</button></div>
      </header>
      <dl className="kv" style={{ marginBottom: 12 }}>
        <dt>ZIP codes</dt><dd>{(area.zips ?? []).length ? (area.zips ?? []).join(', ') : '–'}</dd>
        <dt>Median price, 12 months</dt><dd>{usd(area.median12)}{area.medianLow != null && area.medianHigh != null && <span className="muted"> (95% range {usdCompact(area.medianLow)}–{usdCompact(area.medianHigh)})</span>}</dd>
        <dt>Compared with {city.name}</dt><dd>{gap == null ? '–' : `${pct(gap, 0)} vs the city median`}</dd>
        <dt>Price growth, 1 year</dt><dd>{growth1(area) == null ? 'too few sales' : <>{pct(growth1(area))} <span className="muted">{method(area)}</span></>}{G('medianYoY')}</dd>
        <dt>Price growth, 5 years</dt><dd>{growth5(area) == null ? 'too few sales' : <>{pct(growth5(area), 0)} <span className="muted">{method(area)}</span></>}{G('change5y')}</dd>
        <dt>Price per sq ft</dt><dd>{area.ppsf12 == null ? 'not published' : usd(area.ppsf12)}</dd>
        <dt>Homes sold</dt><dd>{num(area.sales12)} ({pct(area.salesYoY)} in a year){G('sales12')}</dd>
        <dt>Typical rent</dt><dd>{area.rent == null ? 'no estimate' : `${usd(area.rent)} a month (${pct(area.rentYoY)} in a year)`}</dd>
        <dt>Yield</dt><dd>{area.yield == null ? '–' : <>{area.yield.toFixed(1)}% gross{area.netYield != null && <span className="muted">, {area.netYield.toFixed(1)}% net by rule of thumb</span>}</>}{G('yield')}</dd>
        <dt>Reported incidents</dt><dd>{area.violentRate == null ? 'too few residents for a rate' : <span title={area.violentRaw != null ? `Unadjusted: ${area.violentRaw.toFixed(1)} violent, ${area.propertyRaw?.toFixed(1)} property` : undefined}>{`${area.violentRate.toFixed(1)} violent, ${area.propertyRate?.toFixed(1)} property per 1,000 residents, ${trendWord(area)}`}</span>}{area.violentRate != null && <> {G('violentRate')}{G('propertyRate')}</>}</dd>
      </dl>
      <div style={{ marginBottom: 12 }}><MarketNow l={area.listings} area /></div>
      <LineChart labels={data.quarters} series={[{ name: 'Median sale price', color: 'var(--series-1)', values: area.quarterly }]}
        ticks={() => data.quarters.map((q, index) => ({ index, label: q.slice(0, 4), first: q.endsWith('Q1') })).filter((t) => t.first)}
        format={usd} axisFormat={usdCompact} height={230} />
      <p className="footnote" style={{ marginTop: 8 }}>Quarterly median. Quarters with fewer than five sales are left blank. <a href={`#/${city.id}/sales`}>See recent sales</a></p>
    </section>
  )
}
