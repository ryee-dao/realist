import '../styles/drivers.css'
import { useState } from 'react'
import { City, ModelBreakdown, ModelEffect, ModelScore, PriceModel, useData } from '../lib/data'
import { dateLabel, num, pct } from '../lib/format'
import { useWidth } from '../lib/hooks'
import { niceTicks } from '../charts/scale'
import ChartCard from '../charts/ChartCard'
import { Segmented, StatTile } from '../components/bits'

const p0 = (v: number | null | undefined) => (v == null ? '–' : `${v.toFixed(1)}%`)

interface Row {
  key: string; label: string; sub?: string
  pct: number; lo: number | null; hi: number | null
  alt?: { pct: number; lo: number | null; hi: number | null } | null   // second estimate, drawn hollow
  tip: [string, string][]
  ref?: boolean
}

/** Horizontal effects on one % axis: bars (or dots) from 0, with 95% interval whiskers and a hover tooltip per row. */
function EffectChart({ rows, mode, altName, fmt = (v: number) => pct(v), axisFmt = (v: number) => pct(v, 0), label = 'Effects on price, in percent' }: {
  rows: Row[]; mode: 'bar' | 'dot'; altName?: string; fmt?: (v: number) => string; axisFmt?: (v: number) => string; label?: string
}) {
  const [ref, width] = useWidth<HTMLDivElement>()
  const [hover, setHover] = useState<number | null>(null)
  const narrow = width < 560
  const labelW = narrow ? 0 : Math.min(210, width * 0.34)
  const valueW = 58
  const rowH = narrow ? 46 : 32
  const top = 6, bottom = 24
  const lo = Math.min(0, ...rows.flatMap((r) => [r.pct, r.lo ?? r.pct, r.alt?.pct ?? 0, r.alt?.lo ?? 0]))
  const hi = Math.max(0, ...rows.flatMap((r) => [r.pct, r.hi ?? r.pct, r.alt?.pct ?? 0, r.alt?.hi ?? 0]))
  const ticks = niceTicks(lo, hi, narrow ? 4 : 6)
  const x0 = narrow ? 20 : labelW + 4, x1 = Math.max(x0 + 40, width - valueW - 6)
  const x = (v: number) => x0 + ((v - ticks[0]) / (ticks[ticks.length - 1] - ticks[0])) * (x1 - x0)
  const height = top + rows.length * rowH + bottom
  const barY = (i: number) => top + i * rowH + (narrow ? 26 : rowH / 2)

  return (
    <div className="chart" ref={ref} style={{ minHeight: height }}>
      {width > 0 && (
        <svg width={width} height={height} role="img" aria-label={label} onPointerLeave={() => setHover(null)}>
          {ticks.map((t) => (
            <g key={t}>
              <line className={t === 0 ? 'baseline' : 'grid'} x1={x(t)} x2={x(t)} y1={top} y2={height - bottom} />
              <text className="axis" x={x(t)} y={height - 8} textAnchor="middle">{axisFmt(t)}</text>
            </g>
          ))}
          {rows.map((r, i) => {
            const y = barY(i)
            const on = hover == null || hover === i
            return (
              <g key={r.key} opacity={on ? 1 : 0.5} onPointerEnter={() => setHover(i)} onPointerDown={() => setHover(i)}>
                <rect x={0} y={top + i * rowH} width={width} height={rowH} fill="transparent" />
                {narrow
                  ? <text className="eff-label" x={0} y={top + i * rowH + 13}>{r.label}{r.sub && (r.label.length + r.sub.length + 3) * 6.4 < width && <tspan className="eff-sub"> · {r.sub}</tspan>}</text>
                  : <>
                      <text className="eff-label" x={labelW - 6} y={r.sub ? y - 3 : y} dy={r.sub ? 0 : '0.32em'} textAnchor="end">{r.label}</text>
                      {r.sub && <text className="eff-sub" x={labelW - 6} y={y + 10} textAnchor="end">{r.sub}</text>}
                    </>}
                {mode === 'bar' && !r.ref && (
                  <rect className="eff-bar" x={Math.min(x(0), x(r.pct))} y={y - 6} width={Math.max(1.5, Math.abs(x(r.pct) - x(0)))} height={12} rx={3} />
                )}
                {r.lo != null && r.hi != null && !r.ref && (
                  <g className="eff-ci">
                    <line x1={x(r.lo)} x2={x(r.hi)} y1={y} y2={y} />
                    <line x1={x(r.lo)} x2={x(r.lo)} y1={y - 5} y2={y + 5} />
                    <line x1={x(r.hi)} x2={x(r.hi)} y1={y - 5} y2={y + 5} />
                  </g>
                )}
                {mode === 'dot' && r.alt && !r.ref && <circle className="eff-alt" cx={x(r.alt.pct)} cy={y} r={5} />}
                {mode === 'dot' && <circle className={r.ref ? 'eff-ref' : 'eff-dot'} cx={x(r.pct)} cy={y} r={r.ref ? 4 : 5} />}
                <text className="eff-value" x={width - 2} y={y} dy="0.32em" textAnchor="end">{r.ref ? 'base' : fmt(r.pct)}</text>
              </g>
            )
          })}
        </svg>
      )}
      {mode === 'dot' && altName && (
        <div className="legend">
          <span><svg width="12" height="12" aria-hidden="true"><circle className="eff-dot" cx="6" cy="6" r="5" /></svg>All sales since 2019 (95% interval)</span>
          <span><svg width="12" height="12" aria-hidden="true"><circle className="eff-alt" cx="6" cy="6" r="4.5" /></svg>{altName}</span>
        </div>
      )}
      {hover != null && rows[hover] && (
        <div className="tooltip" style={{ left: Math.min(Math.max(0, x(rows[hover].pct) - 90), Math.max(0, width - 250)), top: barY(hover) + 12 }}>
          <div className="tooltip-title">{rows[hover].label}{rows[hover].sub ? ` · ${rows[hover].sub}` : ''}</div>
          {rows[hover].tip.map(([k, v]) => <div key={k} className="tooltip-row"><span>{k}</span><b>{v}</b></div>)}
        </div>
      )}
    </div>
  )
}

const interval = (e: { lo: number | null; hi: number | null }) => (e.lo == null || e.hi == null ? 'n/a' : `${pct(e.lo)} to ${pct(e.hi)}`)

function ladderRows(levels: ModelEffect[], alt: ModelEffect[] | null | undefined): Row[] {
  const altBy = new Map((alt ?? []).map((a) => [a.level, a]))
  return levels.filter((l) => !l.missing).map((l) => {
    const a = altBy.get(l.level)
    return {
      key: l.level, label: l.level, pct: l.pct, lo: l.lo, hi: l.hi, ref: l.ref,
      alt: a && !a.ref ? { pct: a.pct, lo: a.lo, hi: a.hi } : null,
      tip: l.ref ? [['Reference level', 'base'], ['Sales', num(l.n)]] : [
        ['Effect on price', pct(l.pct)], ['95% interval', interval(l)],
        ...(a ? [['2023–24 sales only', pct(a.pct)] as [string, string]] : []), ['Sales', num(l.n)],
      ],
    }
  })
}

const SHORT: Record<string, string> = {
  assessor: 'Assessor, before sale', model: 'Features', nearby: 'Features + nearby',
  adjusted: 'Assessor + adjustments', ppsf: '$/sq ft rule', assessorNow: 'Assessor, current',
}
const TINY: Record<string, string> = {
  assessor: 'Assessor (before)', model: 'Features', nearby: '+ Nearby', adjusted: 'Assessor + adj.', ppsf: '$/sq ft', assessorNow: 'Assessor (now)',
}
const SUB: Record<string, string> = {
  assessor: 'value certified the year before the sale',
  model: 'home features + neighbourhood',
  nearby: '+ prices of nearby earlier sales',
  adjusted: 'assessor before sale + features + nearby',
  ppsf: 'area median $/sq ft × floor area',
  assessorNow: 'set after many of these sales',
}

export default function Drivers({ city }: { city: City }) {
  const { data, error } = useData<PriceModel>(`${city.id}/model.json`)
  const [split, setSplit] = useState<'type' | 'band'>('band')
  const [mode, setMode] = useState<'aligned' | 'forecast'>('aligned')
  if (error) return <div className="notice">Price drivers are not yet available for {city.name}.</div>
  if (!data) return <div className="loading">Loading {city.name}…</div>

  const bt = data.backtest
  const m = Object.fromEntries(bt.methods.map((x) => [x.id, x]))
  const s = (id: string) => m[id]?.[mode]
  const err = (id: string) => s(id)?.mdape ?? null
  const ref = m.assessor ? 'assessor' : 'assessorNow'
  const fair = ['nearby', 'adjusted', 'model'].filter((id) => m[id])
  const best = fair.reduce((a, b) => ((err(b) ?? 99) < (err(a) ?? 99) ? b : a), fair[0])
  const beats = (id: string) => (err(id) ?? 99) < (err(ref) ?? 0)

  const headline = beats('nearby')
    ? `Home features plus nearby sales beat the assessor's value set before the sale: typical error ${p0(err('nearby'))} vs ${p0(err(ref))}.`
    : beats('adjusted')
      ? `Home features and nearby sales alone do not beat the assessor (${p0(err('nearby'))} vs ${p0(err(ref))}), but adjusting the assessor's value with them does: ${p0(err('adjusted'))}.`
      : `The assessor's value is still closest: typical error ${p0(err(ref))}, against ${p0(err(best))} for the best model.`

  const methodRows: Row[] = bt.methods.map((x) => ({
    key: x.id, label: SHORT[x.id] ?? x.name, sub: SUB[x.id], pct: x[mode].mdape ?? 0, lo: null, hi: null,
    tip: [['Typical error', p0(x[mode].mdape)], ['Within ±10%', p0(x[mode].within10)], ['Within ±20%', p0(x[mode].within20)], ['Bias', pct(x[mode].bias)]],
  }))

  const drivers = data.drivers.filter((d) => d.headline)
  const driverRows: Row[] = drivers.map((d) => {
    const h = d.headline as ModelEffect
    return {
      key: d.name, label: d.name,
      sub: d.kind === 'cat' ? `${h.level} vs ${d.ref}` : h.level,
      pct: h.pct, lo: h.lo, hi: h.hi,
      tip: [
        ['Effect on price', pct(h.pct)], ['95% interval', interval(h)],
        ['Error explained', d.oos == null ? '–' : `${d.oos.toFixed(2)}%`],
        ['Recorded for', `${d.coverage.toFixed(0)}% of sales`],
      ],
    }
  })
  const misses = (data.assessorMisses?.drivers ?? []).filter((d) => (d.oos ?? 0) > 0.005).slice(0, 6)
  const cond = data.currentRecord?.conditions ?? {}
  const ladders = ['Interior condition', 'Exterior condition'].map((name) => ({ name, d: data.drivers.find((x) => x.name === name) })).filter((x) => x.d?.levels)
  const rehab = data.currentRecord?.rehabResidualByYear ?? []
  const allYears = data.currentRecord?.allYears?.backtest
  const recent = data.currentRecord?.recentOnly?.backtest
  const ids = bt.methods.map((x) => x.id)
  const breakdown: ModelBreakdown[] = split === 'type' ? bt.byType : bt.byBand
  const pick = (b: ModelBreakdown, id: string) => (mode === 'aligned' ? b[id] as ModelScore | undefined : b.forecast[id])

  const now = m.assessorNow && m.assessor ? ` Today's assessor value, set after many of these sales, reads ${p0(err('assessorNow'))}.` : ''

  return (
    <>
      <p className={`drivers-headline ${beats(best) ? '' : 'flag'}`}>{headline}{now}</p>
      <div className="filters">
        <Segmented label="Time adjustment" value={mode} onChange={setMode}
          options={[{ id: 'aligned', label: 'Quarter level matched' }, { id: 'forecast', label: '2024 level carried forward' }]} />
        <span className="muted">Fit to sales through {dateLabel(bt.trainThrough)}; scored on {num(bt.n)} sales {bt.testFrom ? dateLabel(bt.testFrom) : ''} – {bt.testTo ? dateLabel(bt.testTo) : ''}</span>
      </div>
      <div className="tiles">
        <StatTile hero label={`Typical error · ${SHORT.nearby.toLowerCase()}`} value={p0(err('nearby'))}>
          <span className="muted">median miss, either direction</span>
        </StatTile>
        {m.adjusted && <StatTile label="Typical error · assessor + adjustments" value={p0(err('adjusted'))}>
          <span className="muted">within ±10%: {p0(s('adjusted')?.within10)}</span>
        </StatTile>}
        <StatTile label={`Typical error · ${SHORT[ref].toLowerCase()}`} value={p0(err(ref))}>
          <span className="muted">within ±10%: {p0(s(ref)?.within10)}</span>
        </StatTile>
      </div>

      <div className="split">
        <ChartCard title="How close each method gets" subtitle="Typical error on the same sales, % (shorter is closer)"
          table={{ head: ['Method', 'Typical error', 'Within ±10%', 'Within ±20%', 'Bias'], rows: bt.methods.map((x) => [SHORT[x.id] ?? x.name, p0(x[mode].mdape), p0(x[mode].within10), p0(x[mode].within20), pct(x[mode].bias)]) }}>
          <EffectChart rows={methodRows} mode="bar" fmt={(v) => p0(v)} axisFmt={(v) => `${v}%`} label="Typical error by method" />
          {m.assessorNow && <p className="footnote">"Assessor, current" uses today's value, set after many of these sales: shown for reference, not a forecast.</p>}
        </ChartCard>
        {misses.length > 0 && (
          <section className="card misses">
            <header className="card-head"><div>
              <h3>What the assessor misses</h3>
              <p>Added to the assessor's value, these cut the error most</p>
            </div></header>
            <ol>
              {misses.map((d) => (
                <li key={d.name}>
                  <b>{d.name}</b>
                  <span>{d.headline && d.headline.pct != null ? `${pct(d.headline.pct)} · ${d.headline.level}` : 'location within the city'}</span>
                  <em className="num">{d.oos?.toFixed(2)}%</em>
                </li>
              ))}
            </ol>
            <p className="footnote">Right: share of the error each one removes when added back. Effects are on top of the assessor's value.</p>
          </section>
        )}
      </div>

      <ChartCard title="What moves price" subtitle="Effect on price, other features held equal · ranked by error explained"
        table={{ head: ['Driver', 'Contrast', 'Effect', '95% interval', 'Error explained', 'Recorded'], rows: drivers.map((d) => [d.name, d.headline?.level ?? '', pct(d.headline?.pct), d.headline ? interval(d.headline) : '', d.oos == null ? '–' : `${d.oos.toFixed(2)}%`, `${d.coverage.toFixed(0)}%`]) }}>
        <EffectChart rows={driverRows} mode="bar" />
      </ChartCard>

      <div className="grid-2">
        {ladders.map(({ name, d }) => (
          <ChartCard key={name} title={name} subtitle={`Effect on price vs "${d!.ref}"`}
            table={{ head: ['Rating', 'Effect', '95% interval', '2023–24 only', 'Sales'], rows: d!.levels!.filter((l) => !l.missing).map((l) => {
              const a = cond[name]?.recentOnly?.find((x) => x.level === l.level)
              return [l.level, l.ref ? 'base' : pct(l.pct), l.ref ? '' : interval(l), a && !a.ref ? pct(a.pct) : '–', num(l.n)]
            }) }}>
            <EffectChart rows={ladderRows(d!.levels!, cond[name]?.recentOnly)} mode="dot" altName="Fit on 2023–24 sales only" />
          </ChartCard>
        ))}
      </div>

      <ChartCard title="Typical error by segment" subtitle="% (lower is closer)"
          table={{ head: ['Segment', 'Sales', ...ids.map((id) => `${SHORT[id] ?? id} bias`)], rows: breakdown.map((b) => [b.label, b.n, ...ids.map((id) => pct(pick(b, id)?.bias))]) }}>
          <Segmented label="Segment" value={split} onChange={setSplit}
            options={[{ id: 'band', label: 'Price band' }, { id: 'type', label: 'Home type' }]} />
          <div className="table-scroll">
            <table className="data compact drivers-table">
              <thead><tr><th>Segment</th><th className="num">Sales</th>{ids.map((id) => <th key={id} className="num">{TINY[id] ?? id}</th>)}</tr></thead>
              <tbody>
                {breakdown.map((b) => {
                  const low = Math.min(...ids.filter((id) => id !== 'assessorNow').map((id) => pick(b, id)?.mdape ?? 999))
                  return (
                    <tr key={b.label}><td>{b.label}</td><td className="num">{num(b.n)}</td>
                      {ids.map((id) => <td key={id} className={`num ${pick(b, id)?.mdape === low ? 'best' : ''}`}>{p0(pick(b, id)?.mdape)}</td>)}
                    </tr>
                  )
                })}
                <tr className="total"><td>All</td><td className="num">{num(bt.n)}</td>{ids.map((id) => <td key={id} className="num">{p0(err(id))}</td>)}</tr>
              </tbody>
            </table>
          </div>
          <p className="footnote">Bold: closest, leaving out the current assessor value. Bands are by sale price: every method overestimates cheap sales and underestimates dear ones.</p>
      </ChartCard>

        <section className="card prose drivers-notes">
          <h3>How to read this</h3>
          <ul>
            <li>Each effect compares homes that differ in one feature, in the same neighbourhood and quarter. Whiskers: 95% interval. "Nearby sales" is the typical gap, after features, of the {bt.nearby?.k ?? 15} closest sales in the two years before.</li>
            <li>All models are fitted to sales through 2024 and only use sales dated before each home's sale. "Quarter level matched" removes each method's median miss per 2025–26 quarter, so methods are judged on ranking homes; with no matching, features + nearby are off by {pct(m.nearby?.forecast.bias)}.</li>
            <li>{data.caveat ?? 'Characteristics are the current record.'}{rehab.length > 1 && ` Homes now rated new or rehabbed that sold in ${rehab[0].year} sold ${pct(rehab[0].pct)} vs the model; in ${rehab[rehab.length - 1].year}, ${pct(rehab[rehab.length - 1].pct)}.`}{allYears && recent && ` Fitting 2023–24 sales only: ${p0(recent.mdape)} vs ${p0(allYears.mdape)}.`}</li>
            <li>{data.assessorHistory ? "The assessor's value is the one certified for the sale's tax year, set during the year before." : "The assessor's value is the current one, set after many of these sales, so it is not a strict forecast."} Location enters only as neighbourhood and nearby sales; no demographic data is used.</li>
          </ul>
        </section>
    </>
  )
}
