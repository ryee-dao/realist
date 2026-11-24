import '../styles/crime.css'
import { ReactNode, useEffect, useMemo, useState } from 'react'
import { Area, Areas, City, Grid, useData } from '../lib/data'
import { cssVar, Theme } from '../lib/hooks'
import { monthLabel, num, pct, usd, usdCompact } from '../lib/format'
import { classOf, quintiles, SCALE } from '../components/MapView'
import SectionMap, { escapeHtml, SectionFill } from '../components/SectionMap'
import { Delta, Segmented, StatTile } from '../components/bits'

type Metric = 'violent' | 'property' | 'change' | 'price'
type Rate = 'violent' | 'property'

const METRICS: { id: Metric; label: string }[] = [
  { id: 'violent', label: 'Violent rate' },
  { id: 'property', label: 'Property rate' },
  { id: 'change', label: '1-year change' },
  { id: 'price', label: 'Median price' },
]
const RAMP: Record<Metric, string> = { violent: SCALE.incidents, property: SCALE.incidents, change: SCALE.change, price: SCALE.price }
/** Words at each end of a legend; the swatches between carry the class ranges. */
const ENDS: Record<Metric, [string, string]> = {
  violent: ['Fewer', 'more incidents per 1,000 residents'],
  property: ['Fewer', 'more incidents per 1,000 residents'],
  change: ['Change in reported incidents:', ''],
  price: ['Lower', 'higher price'],
}
const swatch = (m: Metric, cls: number | null | undefined) => (cls == null ? undefined : `var(--${RAMP[m]}-${cls + 1})`)

function Legend({ metric, labels, extra }: { metric: Metric; labels: string[]; extra?: ReactNode }) {
  const [lo, hi] = ENDS[metric]
  return (
    <div className="map-legend">
      <span className="muted">{lo}</span>
      {labels.map((t, i) => <span key={i}><i className="key-box" style={{ background: swatch(metric, i) }} />{t}</span>)}
      {hi && <span className="muted">{hi}</span>}
      {extra}
    </div>
  )
}
const CHANGE_LABELS = ['Down 20%+', 'Down', 'No clear change', 'Up', 'Up 20%+']

const rateOf = (a: Area, r: Rate) => (r === 'violent' ? a.violentRate : a.propertyRate)
const valueOf = (a: Area, m: Metric) => (m === 'change' ? a.crimeYoY : m === 'price' ? a.median12 : rateOf(a, m))
const r1 = (v: number | null | undefined) => (v == null ? '–' : v.toFixed(1))
const fmtMetric = (m: Metric, v: number | null | undefined) => (m === 'price' ? usdCompact(v) : m === 'change' ? pct0(v) : r1(v))
const pct0 = (v: number | null | undefined) => (v != null && Math.abs(v) < 0.5 ? '0%' : pct(v, 0))
/** The year-on-year change, or "no clear change" when it is not distinguishable from chance. */
const changeText = (a: { crimeTrend?: string | null; crimeYoY: number | null }, flat = 'No clear change') =>
  a.crimeTrend === 'flat' ? flat : pct0(a.crimeYoY)
/** 0-4 class for the change map: significant falls and rises either side of a "no clear change" middle. */
const changeClass = (a: Area) => {
  if (a.crimeTrend === 'flat') return 2
  if (a.crimeYoY == null) return null
  return a.crimeYoY <= -20 ? 0 : a.crimeYoY < 0 ? 1 : a.crimeYoY < 20 ? 3 : 4
}

/** Value at quantile q of the non-null values. */
function quantile(values: number[], q: number) {
  const v = [...values].sort((a, b) => a - b)
  return v.length ? v[Math.min(v.length - 1, Math.floor(q * (v.length - 1)))] : 0
}

/* ---------------- map and area panel ---------------- */
interface Classing { cls: (number | null)[]; legend: string[] }

function classify(areas: Area[], m: Metric): Classing {
  if (m === 'change') return { cls: areas.map(changeClass), legend: CHANGE_LABELS }
  const vals = areas.map((a) => valueOf(a, m))
  const b = quintiles(vals.filter((v): v is number => v != null))
  const f = (v: number) => fmtMetric(m, v)
  return {
    cls: vals.map((v) => (v == null ? null : classOf(v, b))),
    legend: b.map((x, i) => (i === 0 ? `Up to ${f(x)}` : i === 4 ? `Over ${f(b[3])}` : `${f(b[i - 1])}–${f(x)}`)),
  }
}

function tipHtml(a: Area) {
  const esc = escapeHtml
  return `<b>${esc(a.name)}</b>${a.group ? ` <span style="color:var(--muted)">${esc(a.group)}</span>` : ''}<br>` +
    `Violent ${r1(a.violentRate)} · Property ${r1(a.propertyRate)} per 1,000<br>` +
    `Change: ${changeText(a, 'no clear change')} · Median ${usdCompact(a.median12)}`
}

function Meter({ label, value, cityValue, lo, hi, log, zero, fmt, text, cityText, color }: {
  color?: string; label: string; value: number | null; cityValue: number | null; lo: number; hi: number; log?: boolean; zero?: boolean; fmt: (v: number | null) => string
  text?: string; cityText?: string
}) {
  const pos = (v: number) => {
    const t = log ? (Math.log(v) - Math.log(lo)) / (Math.log(hi) - Math.log(lo)) : (v - lo) / (hi - lo)
    return Math.max(0, Math.min(1, t)) * 100
  }
  const base = zero ? pos(0) : 0
  const p = value == null ? null : pos(value)
  return (
    <div className="meter-row" title={`${label}: ${text ?? fmt(value)}, city ${cityText ?? fmt(cityValue)}`}>
      <div className="meter-top"><span>{label}</span><span><b>{text ?? fmt(value)}</b> <small>city {cityText ?? fmt(cityValue)}</small></span></div>
      <div className="meter">
        {zero && <i className="meter-zero" style={{ left: `${base}%` }} />}
        {p != null && <i className="meter-fill" style={{ background: color, left: `${Math.min(base, p)}%`, width: `${Math.max(Math.abs(p - base), 1.5)}%` }} />}
        {cityValue != null && <i className="meter-city" style={{ left: `${pos(cityValue)}%` }} />}
      </div>
    </div>
  )
}

function AreaPanel({ city, areas, selected, onSelect }: { city: City; areas: Area[]; selected: number; onSelect: (i: number) => void }) {
  const a = areas[selected]
  const cx = city.context
  const dom = useMemo(() => {
    const vals = (f: (x: Area) => number | null) => areas.map(f).filter((v): v is number => v != null)
    const prices = vals((x) => x.median12).filter((v) => v > 0)
    return {
      violent: quantile(vals((x) => x.violentRate), 0.95),
      property: quantile(vals((x) => x.propertyRate), 0.95),
      change: Math.max(25, quantile(vals((x) => (x.crimeYoY == null ? null : Math.abs(x.crimeYoY))), 0.95)),
      pmin: Math.min(...prices), pmax: Math.max(...prices),
    }
  }, [areas])
  const cls = useMemo(() => Object.fromEntries(METRICS.map((m) => [m.id, classify(areas, m.id).cls])) as Record<Metric, (number | null)[]>, [areas])
  const col = (m: Metric) => swatch(m, cls[m][selected])
  const order = useMemo(() => areas.map((x, i) => ({ x, i })).sort((p, q) => p.x.name.localeCompare(q.x.name)), [areas])
  if (!a) return null
  const top = (v: number, c: number | null, cap: number) => Math.max(cap * 1.1, (c ?? 0) * 1.6, v * 1.05)
  const chg = Math.max(dom.change, Math.abs(a.crimeYoY ?? 0), Math.abs(cx.crimeYoY ?? 0)) * 1.1
  return (
    <aside className="crime-panel">
      <label className="field"><span>Neighbourhood</span>
        <select value={selected} onChange={(e) => onSelect(+e.target.value)}>
          {order.map(({ x, i }) => <option key={i} value={i}>{x.name}{x.group ? ` (${x.group})` : ''}</option>)}
        </select>
      </label>
      <div>
        <h4>{a.name}</h4>
        <div className="sub">{a.residents ? `${num(a.residents)} residents · ` : ''}ZIP {a.zips.join(', ')}</div>
      </div>
      <Meter color={col('violent')} label="Violent per 1,000" value={a.violentRate} cityValue={cx.violentRate} lo={0} hi={top(a.violentRate ?? 0, cx.violentRate, dom.violent)} fmt={r1} />
      <Meter color={col('property')} label="Property per 1,000" value={a.propertyRate} cityValue={cx.propertyRate} lo={0} hi={top(a.propertyRate ?? 0, cx.propertyRate, dom.property)} fmt={r1} />
      <Meter color={col('change')} label="1-year change" value={a.crimeTrend === 'flat' ? 0 : a.crimeYoY} cityValue={cx.crimeTrend === 'flat' ? 0 : cx.crimeYoY} lo={-chg} hi={chg} zero fmt={pct0}
        text={a.crimeTrend === 'flat' ? 'no clear change' : undefined} cityText={cx.crimeTrend === 'flat' ? 'no clear change' : undefined} />
      <Meter color={col('price')} label="Median sale price" value={a.median12} cityValue={city.kpi.median12} lo={dom.pmin} hi={dom.pmax} log fmt={usdCompact} />
    </aside>
  )
}

function Sections({ city, theme, areas, selected, onSelect }: { city: City; theme: Theme; areas: Area[]; selected: number; onSelect: (i: number) => void }) {
  const { data: grid } = useData<Grid>(`${city.id}/grid.json`)
  const [metric, setMetric] = useState<Metric>(() => METRICS.find((m) => m.id === new URLSearchParams(window.location.search).get('metric'))?.id ?? 'violent')
  const classing = useMemo(() => classify(areas, metric), [areas, metric])
  const fillOf = useMemo(() => {
    const colors = [1, 2, 3, 4, 5].map((k) => cssVar(`--${RAMP[metric]}-${k}`))
    return (ai: number): SectionFill | null => { const c = classing.cls[ai]; return c == null ? null : { color: colors[c] } }
  }, [classing, metric, theme])   // eslint-disable-line react-hooks/exhaustive-deps
  const tipOf = useMemo(() => (ai: number) => (areas[ai] ? tipHtml(areas[ai]) : ''), [areas])
  const missing = classing.cls.some((c) => c == null)
  return (
    <>
      <div className="crime-bar" style={{ marginBottom: 10 }}>
        <Segmented label="Map shows" options={METRICS} value={metric} onChange={setMetric} />
      </div>
      <div className="crime-split">
        <div>
          {grid ? <SectionMap className="crime-map" city={city} theme={theme} grid={grid} fillOf={fillOf} tipOf={tipOf} selected={selected} onSelect={onSelect} />
            : <div className="map crime-map loading">Loading the map…</div>}
          <Legend metric={metric} labels={classing.legend} extra={missing && <span><i className="key-box hollow" />No figure</span>} />
        </div>
        <AreaPanel city={city} areas={areas} selected={selected} onSelect={onSelect} />
      </div>
    </>
  )
}

/* ---------------- page ---------------- */
export default function ComparePage({ city, theme }: { city: City; theme: Theme }) {
  const { data } = useData<Areas>(`${city.id}/areas.json`)
  const [selected, setSelected] = useState<number | null>(null)
  const areas = data?.areas ?? []

  // Start on the most populous area with a rate so every view is complete at rest.
  useEffect(() => {
    if (!data || selected != null) return
    let best = 0
    data.areas.forEach((a, i) => { if (a.violentRate != null && (a.residents ?? 0) > (data.areas[best].residents ?? 0)) best = i })
    setSelected(best)
  }, [data, selected])

  const cx = city.context
  const sel = selected ?? 0
  return (
    <>
      <div className="tiles crime-tiles">
        <StatTile label="Violent per 1,000" value={r1(cx.violentRate)}>
          <span className="muted">12 months to {cx.crimeThrough ? monthLabel(cx.crimeThrough) : '–'}</span>
        </StatTile>
        <StatTile label="Property per 1,000" value={r1(cx.propertyRate)}>
          <span className="muted">12 months, citywide</span>
        </StatTile>
        <StatTile label="Incidents, 1 year" value={changeText(cx)}><span className="muted">vs the year before</span></StatTile>
        <StatTile label="Median sale price" value={usd(city.kpi.median12)}><Delta value={city.kpi.medianYoY} suffix="in a year" /></StatTile>
      </div>

      <section className="card">
        <header className="card-head">
          <div><h3>Reported incidents by neighbourhood</h3></div>
        </header>
        {!data ? <div className="loading">Loading…</div>
          : <Sections city={city} theme={theme} areas={areas} selected={sel} onSelect={setSelected} />}
      </section>

      <p className="footnote">
        Police reports from the <a href={city.crimeLink} target="_blank" rel="noreferrer">{city.crimeSource}</a>, last 12 months. Violent: homicide, rape, robbery, aggravated assault. Property: burglary, theft, vehicle theft, arson.<br />
        Rates are per resident, so busy places with few residents read high. Where few people live, rates are pulled toward the city rate. A change shows only when it is unlikely to be chance.
      </p>
    </>
  )
}
