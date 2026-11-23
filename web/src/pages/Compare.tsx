import '../styles/crime.css'
import { ReactNode, useEffect, useMemo, useRef, useState } from 'react'
import L from 'leaflet'
import { Area, Areas, City, Grid, useData } from '../lib/data'
import { cssVar, Theme, useWidth } from '../lib/hooks'
import { monthLabel, num, pct, usd, usdCompact } from '../lib/format'
import { classOf, quintiles, SCALE } from '../components/MapView'
import { Delta, Segmented, StatTile } from '../components/bits'
import { niceTicks } from '../charts/scale'

type StyleId = 'sections' | 'ranked' | 'scatter'
type Metric = 'violent' | 'property' | 'change' | 'price'
type Rate = 'violent' | 'property'

const STYLES: { id: StyleId; label: string }[] = [
  { id: 'sections', label: 'Sections' },
  { id: 'ranked', label: 'Ranked' },
  { id: 'scatter', label: 'Scatter' },
]
const METRICS: { id: Metric; label: string }[] = [
  { id: 'violent', label: 'Violent rate' },
  { id: 'property', label: 'Property rate' },
  { id: 'change', label: '1-year change' },
  { id: 'price', label: 'Median price' },
]
const RATES: { id: Rate; label: string }[] = [
  { id: 'violent', label: 'Violent' },
  { id: 'property', label: 'Property' },
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
const arrow = (a: Area) => (a.crimeTrend === 'up' ? '▲' : a.crimeTrend === 'down' ? '▼' : a.crimeTrend === 'flat' ? '■' : '')
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

function readStyle(): StyleId {
  const s = new URLSearchParams(window.location.search).get('style')
  return s === 'ranked' || s === 'scatter' ? s : 'sections'
}

/* ---------------- shared hover tooltip for the SVG / list views ---------------- */
interface TipState { x: number; y: number; body: ReactNode }
function useTip() {
  const wrap = useRef<HTMLDivElement>(null)
  const [tip, setTip] = useState<TipState | null>(null)
  const show = (e: React.MouseEvent, body: ReactNode) => {
    const r = wrap.current!.getBoundingClientRect()
    setTip({ x: e.clientX - r.left, y: e.clientY - r.top, body })
  }
  const node = tip && (() => {
    const w = wrap.current?.clientWidth ?? 0
    const left = tip.x > w - 220 ? Math.max(0, tip.x - 214) : tip.x + 14
    return <div className="tooltip crime-tip" style={{ left, top: tip.y + 14 }}>{tip.body}</div>
  })()
  return { wrap, show, hide: () => setTip(null), node }
}

function AreaTip({ a, rate }: { a: Area; rate?: Rate }) {
  const rows: [string, string][] = []
  if (!rate || rate === 'violent') rows.push(['Violent per 1,000', r1(a.violentRate)])
  if (!rate || rate === 'property') rows.push(['Property per 1,000', r1(a.propertyRate)])
  rows.push(['1-year change', changeText(a)], ['Median sale', usd(a.median12)], ['Sales, 12 months', num(a.sales12)])
  return (
    <>
      <div className="tooltip-title">{a.name}{a.group ? <span className="muted"> · {a.group}</span> : null}</div>
      {rows.map(([k, v]) => <div className="tooltip-row" key={k}><span>{k}</span><b>{v}</b></div>)}
    </>
  )
}

/* ---------------- 1. Sections ---------------- */
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
  const esc = (s: string) => s.replace(/[&<>]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]!))
  return `<b>${esc(a.name)}</b>${a.group ? ` <span style="color:var(--muted)">${esc(a.group)}</span>` : ''}<br>` +
    `Violent ${r1(a.violentRate)} · Property ${r1(a.propertyRate)} per 1,000<br>` +
    `Change: ${changeText(a, 'no clear change')} · Median ${usdCompact(a.median12)}`
}

function SectionsMap({ city, theme, grid, areas, metric, classing, selected, onSelect }: {
  city: City; theme: Theme; grid: Grid; areas: Area[]; metric: Metric; classing: Classing; selected: number; onSelect: (i: number) => void
}) {
  const el = useRef<HTMLDivElement>(null)
  const map = useRef<L.Map | null>(null)
  const fill = useRef<L.LayerGroup | null>(null)
  const outline = useRef<L.LayerGroup | null>(null)
  const renderer = useRef<L.Canvas | null>(null)
  const select = useRef(onSelect)
  select.current = onSelect

  useEffect(() => {
    const m = L.map(el.current!, { scrollWheelZoom: false, zoomSnap: 0.5 }).setView(city.center, city.zoom)
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors', maxZoom: 17,
    }).addTo(m)
    m.on('click', () => m.scrollWheelZoom.enable())
    m.on('mouseout', () => m.scrollWheelZoom.disable())
    renderer.current = L.canvas({ padding: 0.5 })
    fill.current = L.layerGroup().addTo(m)
    outline.current = L.layerGroup().addTo(m)
    map.current = m
    const g = grid.columns
    const pts: L.LatLngTuple[] = []
    for (let i = 0; i < g.lat.length; i++) if (g.area[i] >= 0) pts.push([g.lat[i], g.lon[i]])
    if (pts.length) m.fitBounds(L.latLngBounds(pts), { padding: [8, 8] })
    return () => { m.remove(); map.current = null }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    const group = fill.current!
    group.clearLayers()
    const colors = [1, 2, 3, 4, 5].map((k) => cssVar(`--${RAMP[metric]}-${k}`))
    const g = grid.columns
    const h = grid.cell / 2
    for (let i = 0; i < g.lat.length; i++) {
      const ai = g.area[i]
      if (ai < 0 || !areas[ai]) continue
      const c = classing.cls[ai]
      const a = areas[ai]
      L.rectangle([[g.lat[i] - h, g.lon[i] - h], [g.lat[i] + h, g.lon[i] + h]], {
        renderer: renderer.current!, stroke: false,
        fillColor: c == null ? cssVar('--surface-2') : colors[c], fillOpacity: c == null ? 0 : 0.85,
      })
        .bindTooltip(() => tipHtml(a), { className: 'map-tip', sticky: true })
        .on('click', (e) => { L.DomEvent.stopPropagation(e); select.current(ai) })
        .addTo(group)
    }
  }, [grid, areas, metric, classing, theme])

  // Outline the selected area: draw only the cell edges that border another area.
  useEffect(() => {
    const group = outline.current!
    group.clearLayers()
    const g = grid.columns
    const s = grid.cell
    const h = s / 2
    const key = (la: number, lo: number) => `${Math.round(la / s)},${Math.round(lo / s)}`
    const mine = new Set<string>()
    for (let i = 0; i < g.lat.length; i++) if (g.area[i] === selected) mine.add(key(g.lat[i], g.lon[i]))
    const segs: L.LatLngTuple[][] = []
    for (let i = 0; i < g.lat.length; i++) {
      if (g.area[i] !== selected) continue
      const la = g.lat[i], lo = g.lon[i]
      if (!mine.has(key(la + s, lo))) segs.push([[la + h, lo - h], [la + h, lo + h]])
      if (!mine.has(key(la - s, lo))) segs.push([[la - h, lo - h], [la - h, lo + h]])
      if (!mine.has(key(la, lo + s))) segs.push([[la - h, lo + h], [la + h, lo + h]])
      if (!mine.has(key(la, lo - s))) segs.push([[la - h, lo - h], [la + h, lo - h]])
    }
    if (segs.length) L.polyline(segs, { renderer: renderer.current!, color: cssVar('--text'), weight: 2.5, interactive: false }).addTo(group)
  }, [grid, selected, theme])

  return <div ref={el} className="map crime-map" />
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
      <div className="map-legend" style={{ marginTop: 0 }}>
        <span><i className="key-line city" />City figure</span>
        <span className="muted">Bar colour matches the map</span>
      </div>
    </aside>
  )
}

function Sections({ city, theme, areas, selected, onSelect }: { city: City; theme: Theme; areas: Area[]; selected: number; onSelect: (i: number) => void }) {
  const { data: grid } = useData<Grid>(`${city.id}/grid.json`)
  const [metric, setMetric] = useState<Metric>(() => METRICS.find((m) => m.id === new URLSearchParams(window.location.search).get('metric'))?.id ?? 'violent')
  const classing = useMemo(() => classify(areas, metric), [areas, metric])
  const missing = classing.cls.some((c) => c == null)
  return (
    <>
      <div className="crime-bar" style={{ marginBottom: 10 }}>
        <Segmented label="Map shows" options={METRICS} value={metric} onChange={setMetric} />
      </div>
      <div className="crime-split">
        <div>
          {grid ? <SectionsMap city={city} theme={theme} grid={grid} areas={areas} metric={metric} classing={classing} selected={selected} onSelect={onSelect} />
            : <div className="map crime-map loading">Loading the map…</div>}
          <Legend metric={metric} labels={classing.legend} extra={missing && <span><i className="key-box hollow" />No figure</span>} />
        </div>
        <AreaPanel city={city} areas={areas} selected={selected} onSelect={onSelect} />
      </div>
    </>
  )
}

/* ---------------- 2. Ranked ---------------- */
function Ranked({ city, areas, selected, onSelect }: { city: City; areas: Area[]; selected: number; onSelect: (i: number) => void }) {
  const [rate, setRate] = useState<Rate>('violent')
  const [q, setQ] = useState('')
  const [desc, setDesc] = useState(true)
  const tip = useTip()
  const cityRate = rate === 'violent' ? city.context.violentRate : city.context.propertyRate
  const withRate = useMemo(() => areas.map((a, i) => ({ a, i, v: rateOf(a, rate) })).filter((r): r is { a: Area; i: number; v: number } => r.v != null), [areas, rate])
  const left = areas.length - withRate.length
  const classing = useMemo(() => classify(areas, rate), [areas, rate])
  // Cap the scale near the 97th percentile so one business district does not flatten every other bar.
  const cap = useMemo(() => {
    const vs = withRate.map((r) => r.v)
    const max = Math.max(...vs)
    const c = Math.max(quantile(vs, 0.97) * 1.1, (cityRate ?? 0) * 2)
    return max <= c * 1.3 ? max : niceTicks(0, c, 4).pop()!
  }, [withRate, cityRate])
  const rows = useMemo(() => {
    const s = q.trim().toLowerCase()
    const hit = (a: Area) => !s || a.name.toLowerCase().includes(s) || a.group.toLowerCase().includes(s) || a.zips.some((z) => z.startsWith(s))
    return withRate.filter((r) => hit(r.a)).sort((x, y) => (desc ? y.v - x.v : x.v - y.v))
  }, [withRate, q, desc])
  const cityPos = cityRate == null ? null : Math.min(100, (cityRate / cap) * 100)

  return (
    <>
      <div className="crime-bar" style={{ marginBottom: 10 }}>
        <Segmented label="Rate" options={RATES} value={rate} onChange={setRate} />
        <div className="rank-tools">
          <label className="field"><input type="search" placeholder="Name or ZIP" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search neighbourhoods by name or ZIP" /></label>
          <button className="ghost" onClick={() => setDesc(!desc)}>{desc ? 'Highest first ↓' : 'Lowest first ↑'}</button>
        </div>
      </div>
      <Legend metric={rate} labels={classing.legend} />
      <div className="crime-wrap" ref={tip.wrap} onMouseLeave={tip.hide} style={{ marginTop: 8 }}>
        <div className="rank-list">
          <div className="rank-row head">
            <span className="rank-name">{rows.length} of {withRate.length}</span>
            <span className="rank-bar">{cityPos != null && <span className="rank-city-label" style={{ left: `${Math.max(6, Math.min(94, cityPos))}%` }}>City {r1(cityRate)}</span>}</span>
            <span className="rank-rate">Per 1k</span>
            <span className="rank-chg">1 yr</span>
            <span className="rank-price">Median</span>
          </div>
          {rows.map(({ a, i, v }) => (
            <div key={i} className={`rank-row ${i === selected ? 'sel' : ''}`} onClick={() => onSelect(i)}
              onMouseMove={(e) => tip.show(e, <AreaTip a={a} />)}>
              <span className="rank-name">{a.name}{a.group ? <small>{a.group}</small> : null}</span>
              <span className="rank-bar">
                <i className={`rank-fill ${v > cap ? 'over' : ''}`} style={{ background: swatch(rate, classing.cls[i]), width: `${Math.max(0.6, Math.min(100, (v / cap) * 100))}%` }} />
                {cityPos != null && <i className="rank-city" style={{ left: `${cityPos}%` }} />}
              </span>
              <span className="rank-rate">{r1(v)}</span>
              <span className="rank-chg"><span className="arrow" aria-hidden="true">{arrow(a)}</span> {a.crimeTrend === 'flat' ? <span className="flat">no clear change</span> : pct0(a.crimeYoY)}</span>
              <span className="rank-price">{usdCompact(a.median12)}</span>
            </div>
          ))}
          {!rows.length && <div className="loading" style={{ padding: 30 }}>No match.</div>}
        </div>
        {tip.node}
      </div>
      <p className="rank-note">
        Bars past {r1(cap)} are cut short (marked with a break).
        {left > 0 && ` ${left} ${left === 1 ? 'area is' : 'areas are'} left out: too few residents for a rate.`}
      </p>
    </>
  )
}

/* ---------------- 3. Scatter ---------------- */
function logTicks(lo: number, hi: number, width: number): number[] {
  const all: number[] = []
  for (let p = Math.floor(Math.log10(lo)); p <= Math.ceil(Math.log10(hi)); p++) for (const m of [1, 2, 5]) all.push(m * 10 ** p)
  let t = all.filter((v) => v >= lo && v <= hi)
  if (t.length > width / 64) t = t.filter((v) => !/^2/.test(String(v)))
  if (t.length > width / 64) t = t.filter((v) => /^1/.test(String(v)))
  return t
}

function Scatter({ city, areas, selected, onSelect }: { city: City; areas: Area[]; selected: number; onSelect: (i: number) => void }) {
  const [rate, setRate] = useState<Rate>('violent')
  const [ref, width] = useWidth<HTMLDivElement>()
  const tip = useTip()
  const W = Math.max(width, 300)
  const wide = W >= 700
  const H = W < 560 ? 420 : 520
  const M = { l: 44, r: 16, t: 52, b: wide ? 56 : 72 }
  const cityRate = rate === 'violent' ? city.context.violentRate : city.context.propertyRate
  const cityPrice = city.kpi.median12

  const classing = useMemo(() => classify(areas, rate), [areas, rate])
  const pts = useMemo(() => areas.map((a, i) => ({ a, i, x: a.median12, y: rateOf(a, rate) }))
    .filter((p): p is { a: Area; i: number; x: number; y: number } => p.x != null && p.x > 0 && p.y != null), [areas, rate])

  const view = useMemo(() => {
    if (!pts.length) return null
    const xs = pts.map((p) => p.x), ys = pts.map((p) => p.y)
    const xlo = Math.min(...xs) / 1.12, xhi = Math.max(...xs) * 1.12
    const ymaxRaw = Math.max(...ys)
    // Cap the y-axis near the 98th percentile; the few above it sit on the top edge as triangles.
    const want = Math.max(quantile(ys, 0.98) * 1.15, (cityRate ?? 0) * 2)
    const yt = niceTicks(0, ymaxRaw <= want * 1.3 ? ymaxRaw : want, 5)
    const ymax = yt[yt.length - 1]
    const iw = W - M.l - M.r, ih = H - M.t - M.b
    const sx = (v: number) => M.l + ((Math.log(v) - Math.log(xlo)) / (Math.log(xhi) - Math.log(xlo))) * iw
    const sy = (v: number) => M.t + ih - (Math.min(v, ymax) / ymax) * ih
    const smax = Math.max(...pts.map((p) => p.a.sales12), 1)
    const rad = (s: number) => 3 + Math.sqrt(s / smax) * (W < 560 ? 9 : 13)
    return { xlo, xhi, yt, ymax, sx, sy, rad, iw, ih, over: pts.filter((p) => p.y > ymax).length }
  }, [pts, W, H, cityRate]) // eslint-disable-line react-hooks/exhaustive-deps

  const labels = useMemo(() => {
    if (!view) return []
    const byY = [...pts].sort((p, q) => q.y - p.y)
    const byX = [...pts].sort((p, q) => q.x - p.x)
    const pick = [pts.find((p) => p.i === selected), byY[0], byY[1], byX[0], byX[byX.length - 1], byY[byY.length - 1]]
    const boxes: { x: number; y: number; w: number }[] = []
    if (cityPrice != null) boxes.push({ x: view.sx(cityPrice), y: M.t, w: 80 })
    if (cityRate != null) boxes.push({ x: W - M.r - 80, y: view.sy(cityRate) - 14, w: 80 })
    const out: { i: number; x: number; y: number; end: boolean; text: string }[] = []
    for (const p of pick) {
      if (!p || out.some((o) => o.i === p.i)) continue
      const cx = view.sx(p.x), cy = view.sy(p.y), r = view.rad(p.a.sales12)
      const w = p.a.name.length * 6.6
      const end = cx + r + 4 + w > W - M.r
      const x = end ? cx - r - 4 - w : cx + r + 4
      const y = Math.max(M.t + 12, cy + 4)
      if (x < M.l || boxes.some((b) => x < b.x + b.w && x + w > b.x && Math.abs(y - b.y - 6) < 14)) continue
      boxes.push({ x, y: y - 10, w })
      out.push({ i: p.i, x: end ? x + w : x, y, end, text: p.a.name })
    }
    return out
  }, [pts, view, selected, W, H, cityPrice, cityRate]) // eslint-disable-line react-hooks/exhaustive-deps

  const xt = view ? logTicks(view.xlo, view.xhi, view.iw) : []
  const rateWord = rate === 'violent' ? 'Violent' : 'Property'
  return (
    <>
      <div className="crime-bar" style={{ marginBottom: 6 }}>
        <Segmented label="Rate" options={RATES} value={rate} onChange={setRate} />
        <div className="map-legend" style={{ marginTop: 0 }}>
          <span><i className="key-box" style={{ background: 'var(--text-2)', borderRadius: '50%' }} />Dot size = sales in 12 months</span>
          <span><i className="key-line" style={{ background: 'var(--line-strong)' }} />City figure</span>
        </div>
      </div>
      <Legend metric={rate} labels={classing.legend} />
      <div ref={ref} className="crime-wrap" onMouseLeave={tip.hide}>
        <div className="scatter-scroll">
          {view && width > 0 && (
            <svg className="chart scatter" width={W} height={H} role="img" aria-label={`${rateWord} reported incidents per 1,000 residents against median sale price`}>
              <g>
                {view.yt.map((t) => <g key={t}>
                  <line className={t === 0 ? 'baseline' : 'grid'} x1={M.l} x2={W - M.r} y1={view.sy(t)} y2={view.sy(t)} style={{ stroke: t === 0 ? 'var(--line-strong)' : 'var(--grid)' }} />
                  <text className="axis" x={M.l - 6} y={view.sy(t) + 3.5} textAnchor="end" style={{ fill: 'var(--muted)', font: '10.5px var(--mono)' }}>{t}</text>
                </g>)}
                {xt.map((t) => <text key={t} className="axis" x={view.sx(t)} y={H - M.b + 16} textAnchor="middle" style={{ fill: 'var(--muted)', font: '10.5px var(--mono)' }}>{usdCompact(t)}</text>)}
                <text className="axis-title" x={M.l - 38} y={14}>{rateWord} reported incidents per 1,000 residents</text>
                <text className="axis-title" x={wide ? M.l + view.iw / 2 : W - M.r} y={H - M.b + (wide ? 36 : 56)} textAnchor={wide ? 'middle' : 'end'}>Median sale price, log scale</text>
              </g>
              {cityPrice != null && <>
                <line className="guide" x1={view.sx(cityPrice)} x2={view.sx(cityPrice)} y1={M.t} y2={H - M.b} />
                <text className="guide-lab halo" x={view.sx(cityPrice) + 4} y={M.t + 12}>City {usdCompact(cityPrice)}</text>
              </>}
              {cityRate != null && <>
                <line className="guide" x1={M.l} x2={W - M.r} y1={view.sy(cityRate)} y2={view.sy(cityRate)} />
                <text className="guide-lab halo" x={W - M.r - 4} y={view.sy(cityRate) - 4} textAnchor="end">City {r1(cityRate)}</text>
              </>}
              <g>
                <text className="quad" x={wide ? M.l : 2} y={M.t - 12}>↖ Lower price, higher rate</text>
                <text className="quad" x={W - M.r} y={M.t - 12} textAnchor="end">Higher price, higher rate ↗</text>
                <text className="quad" x={wide ? M.l : 2} y={H - M.b + 36}>↙ Lower price, lower rate</text>
                <text className="quad" x={W - M.r} y={H - M.b + 36} textAnchor="end">Higher price, lower rate ↘</text>
              </g>
              <g>
                {[...pts].sort((p, q) => q.a.sales12 - p.a.sales12).map((p) => {
                  const cx = view.sx(p.x), cy = view.sy(p.y), r = view.rad(p.a.sales12)
                  const on = p.i === selected
                  const fill = { style: { fill: swatch(rate, classing.cls[p.i]) } }
                  const events = { onClick: () => onSelect(p.i), onMouseMove: (e: React.MouseEvent) => tip.show(e, <AreaTip a={p.a} rate={rate} />) }
                  return p.y > view.ymax
                    ? <path key={p.i} className={`dot ${on ? 'sel' : ''}`} d={`M${cx},${cy - 7}L${cx + 7},${cy + 5}L${cx - 7},${cy + 5}Z`} {...fill} {...events} />
                    : <circle key={p.i} className={`dot ${on ? 'sel' : ''}`} cx={cx} cy={cy} r={r} {...fill} {...events} />
                })}
              </g>
              {labels.map((l) => <text key={l.i} className="lab halo" x={l.x} y={l.y} textAnchor={l.end ? 'end' : 'start'}>{l.text}</text>)}
            </svg>
          )}
        </div>
        {tip.node}
      </div>
      {view && view.over > 0 && <p className="rank-note">▲ {view.over} above {view.ymax}, drawn on the top edge.</p>}
    </>
  )
}

/* ---------------- page ---------------- */
export default function ComparePage({ city, theme }: { city: City; theme: Theme }) {
  const { data } = useData<Areas>(`${city.id}/areas.json`)
  const [style, setStyleState] = useState<StyleId>(readStyle)
  const [selected, setSelected] = useState<number | null>(null)
  const areas = data?.areas ?? []

  const setStyle = (s: StyleId) => {
    setStyleState(s)
    const u = new URL(window.location.href)
    u.searchParams.set('style', s)
    window.history.replaceState(null, '', u.toString())
  }

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
      <div className="tiles">
        <StatTile label="Violent per 1,000 residents" value={r1(cx.violentRate)}>
          <span className="muted">12 months to {cx.crimeThrough ? monthLabel(cx.crimeThrough) : '–'}</span>
        </StatTile>
        <StatTile label="Property per 1,000 residents" value={r1(cx.propertyRate)}>
          <span className="muted">12 months, citywide</span>
        </StatTile>
        <StatTile label="Reported incidents, 1 year" value={changeText(cx)}>{cx.crimeTrend === 'flat' ? <span className="muted">vs year before</span> : <Delta value={cx.crimeYoY} suffix="vs year before" />}</StatTile>
        <StatTile label="Median sale price" value={usd(city.kpi.median12)}><Delta value={city.kpi.medianYoY} suffix="vs year before" /></StatTile>
      </div>

      <section className="card">
        <header className="card-head">
          <div><h3>Reported incidents by neighbourhood</h3></div>
          <div className="field" style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
            <span>Style preview</span>
            <Segmented label="Style preview" options={STYLES} value={style} onChange={setStyle} />
          </div>
        </header>
        {!data ? <div className="loading">Loading…</div>
          : style === 'sections' ? <Sections city={city} theme={theme} areas={areas} selected={sel} onSelect={setSelected} />
          : style === 'ranked' ? <Ranked city={city} areas={areas} selected={sel} onSelect={setSelected} />
          : <Scatter city={city} areas={areas} selected={sel} onSelect={setSelected} />}
      </section>

      <p className="footnote">
        Reported incidents recorded by the <a href={city.crimeLink} target="_blank" rel="noreferrer">{city.crimeSource}</a>, last 12 months. Violent: homicide, rape, robbery, aggravated assault. Property: burglary, theft, vehicle theft, arson.<br />
        Rates are per resident, so places with many visitors read high. Rates are adjusted toward the city rate where few people live, and a change is shown only when it is unlikely to be chance. No demographic data is used.
      </p>
    </>
  )
}
