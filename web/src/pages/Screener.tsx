import '../styles/finder.css'
import { useEffect, useMemo, useRef, useState } from 'react'
import { Area, Areas, City, useData } from '../lib/data'
import { Theme } from '../lib/hooks'
import { num, pct, usdCompact } from '../lib/format'
import { gradeOf, gradeStyle, percentiles, rankPool, Robust, robustness, Tier, TIER_SEQ, TIERS, tierStyle, useRamp, WEAK_BELOW } from '../lib/grades'
import MapView, { MapPoint } from '../components/MapView'
import { Segmented, SortHead } from '../components/bits'
import MarketNow from '../components/MarketNow'

type Factor = 'yield' | 'growth1' | 'growth5' | 'price' | 'activity' | 'crime'
type Weights = Record<Factor, number>

const FACTORS: { id: Factor; label: string; short: string; hint: string }[] = [
  { id: 'yield', label: 'Rental yield', short: 'Yield', hint: 'A year of typical rent as a share of the typical sale price' },
  { id: 'growth1', label: 'Growth, 1 yr', short: '1-yr growth', hint: 'Change in the median sale price over a year' },
  { id: 'growth5', label: 'Growth, 5 yr', short: '5-yr growth', hint: 'Change in the median sale price over five years' },
  { id: 'price', label: 'Lower price', short: 'Price', hint: 'A lower median sale price scores higher' },
  { id: 'activity', label: 'Active market', short: 'Sold', hint: 'More homes sold in 12 months, so easier to buy and resell' },
  { id: 'crime', label: 'Fewer incidents', short: 'Incidents', hint: 'Police-reported violent and property incidents per 1,000 residents; fewer scores higher' },
]
const PRESETS: { id: string; label: string; w: Weights }[] = [
  { id: 'rental', label: 'Rental income', w: { yield: 5, growth1: 1, growth5: 1, price: 1, activity: 2, crime: 1 } },
  { id: 'growth', label: 'Price growth', w: { yield: 0, growth1: 4, growth5: 4, price: 0, activity: 2, crime: 1 } },
  { id: 'entry', label: 'Low entry price', w: { yield: 2, growth1: 1, growth5: 0, price: 5, activity: 2, crime: 1 } },
  { id: 'balanced', label: 'Balanced', w: { yield: 2, growth1: 2, growth5: 2, price: 2, activity: 2, crime: 2 } },
]

// best available growth from the engine (repeat-sales where possible), else change in the median
const g1 = (a: Area) => a.priceGrowth1 ?? a.medianYoY
const g5 = (a: Area) => a.priceGrowth5 ?? a.change5y

/** The raw number behind each factor, oriented so that higher is better. */
function raw(a: Area, f: Factor): number | null {
  switch (f) {
    case 'yield': return a.yield
    case 'growth1': return g1(a)
    case 'growth5': return g5(a)
    case 'price': return a.median12 == null ? null : -a.median12
    case 'activity': return a.sales12
    case 'crime': return a.violentRate == null || a.propertyRate == null ? null : -(a.violentRate * 3 + a.propertyRate)   // violent incidents weigh three times
  }
}

/** Short display value for a factor. */
function value(a: Area, f: Factor): string | null {
  if (raw(a, f) == null) return null
  switch (f) {
    case 'yield': return `${a.yield!.toFixed(1)}%`
    case 'growth1': return pct(g1(a))
    case 'growth5': return pct(g5(a), 0)
    case 'price': return usdCompact(a.median12)
    case 'activity': return num(a.sales12)
    case 'crime': return `${a.violentRate!.toFixed(1)} / ${a.propertyRate!.toFixed(0)}`
  }
}
const UNIT: Partial<Record<Factor, string>> = { activity: 'homes sold in 12 months', crime: 'violent / property incidents per 1,000 residents' }

interface Scored { area: Area; score: number; pctl: Partial<Record<Factor, number>>; missing: Factor[]; rank: number; tier: Tier; demoted: boolean }
const IDS = FACTORS.map((f) => f.id)
const HOW = 'Each area is ranked within the city on each factor (0–100). The score is a weighted geometric average of those ranks, so a weak result on something you weight heavily pulls the score down more than a simple average would. A slider says how much you care about going from the city\'s worst to its best on that factor. "Must" hides areas in the bottom 40% on that factor. Missing data counts as the city median and keeps an area out of S. An area weak (D or E) on a factor weighted 4 or 5 cannot be S.'

/** Optional URL seeds for screenshots and sharing: ?w=5,1,1,1,2,5 (factor order) and ?must=crime,yield */
function seed(): { w: Weights | null; must: Factor[] } {
  const q = new URLSearchParams(window.location.search)
  const nums = (q.get('w') ?? '').split(',').map(Number)
  const w = nums.length === 6 && nums.every((n) => Number.isInteger(n) && n >= 0 && n <= 5) && nums.some((n) => n > 0)
    ? Object.fromEntries(IDS.map((id, i) => [id, nums[i]])) as Weights : null
  const must = (q.get('must') ?? '').split(',').filter((x): x is Factor => (IDS as string[]).includes(x))
  return { w, must }
}
const SEED = seed()
const presetFor = (w: Weights) => PRESETS.find((p) => IDS.every((id) => p.w[id] === w[id]))?.id ?? 'custom'

export default function ScreenerPage({ city, theme }: { city: City; theme: Theme }) {
  const { data } = useData<Areas>(`${city.id}/areas.json`)
  const [w, setW] = useState<Weights>(SEED.w ?? PRESETS[0].w)
  const [preset, setPreset] = useState(() => presetFor(SEED.w ?? PRESETS[0].w))
  const [must, setMust] = useState<Factor[]>(SEED.must)
  const [budget, setBudget] = useState('')
  const [minSales, setMinSales] = useState(30)
  const [picked, setPicked] = useState<string | null>(null)
  const [sort, setSort] = useState<{ key: string; dir: 1 | -1 }>({ key: 'rank', dir: 1 })
  const ramp = useRamp(theme)
  const detailRef = useRef<HTMLElement>(null)
  const [allTiers, setAllTiers] = useState(false)

  // percentile of each area within the pool, per factor (mid-rank, ties share a percentile)
  const pool = useMemo(() => {
    const max = parseFloat(budget) || Infinity
    const list = (data?.areas ?? []).filter((a) => a.median12 != null && a.median12 <= max && a.sales12 >= minSales)
    const pc = FACTORS.map((f) => percentiles(list, (a) => a.key, (a) => raw(a, f.id)))
    return list.map((a) => ({ area: a, key: a.key, pctl: Object.fromEntries(IDS.flatMap((id, i) => (pc[i].has(a.key) ? [[id, pc[i].get(a.key)]] : []))) as Partial<Record<Factor, number>> }))
  }, [data, budget, minSales])

  const { scored, hidden } = useMemo(() => {
    const byKey = new Map(pool.map((r) => [r.key, r]))
    const r = rankPool(pool, IDS, w, must)
    return {
      scored: r.ranked.map((x): Scored => ({ area: byKey.get(x.key)!.area, pctl: byKey.get(x.key)!.pctl, score: x.score, rank: x.rank, tier: x.tier, missing: x.incomplete, demoted: x.demoted })),
      hidden: r.hidden.map((h) => ({ area: byKey.get(h.key)!.area, reasons: h.reasons })),
    }
  }, [pool, w, must])

  // robustness readout: 200 re-rankings with nudged weights, computed a moment after the inputs settle
  const [robust, setRobust] = useState<{ for: unknown; map: Map<string, Robust> } | null>(null)
  useEffect(() => {
    const id = window.setTimeout(() => setRobust({ for: scored, map: robustness(pool, IDS, w, must) }), 250)
    return () => window.clearTimeout(id)
  }, [pool, w, must, scored])
  const rb = robust?.for === scored ? robust.map : null

  const points: MapPoint[] = useMemo(() => scored.filter((s) => s.area.lat != null).map((s) => ({
    id: s.area.key, lat: s.area.lat as number, lon: s.area.lon as number, radius: s.tier === 'S' ? 11 : s.tier === 'A' ? 9 : 7,
    color: ramp[TIER_SEQ[s.tier]].bg,
    tip: `${s.tier} tier · #${s.rank} ${s.area.name} · score ${s.score}`,
  })).reverse(), [scored, ramp])

  const rows = useMemo(() => {
    if (sort.key === 'rank') return sort.dir === 1 ? scored : [...scored].reverse()
    if (sort.key === 'name') return [...scored].sort((a, b) => a.area.name.localeCompare(b.area.name) * sort.dir)
    const f = sort.key as Factor
    return [...scored].sort((a, b) => {
      const x = a.pctl[f], y = b.pctl[f]
      if (x == null) return y == null ? 0 : 1
      if (y == null) return -1
      return (y - x) * sort.dir
    })
  }, [scored, sort])

  if (!data) return <div className="loading">Loading areas…</div>

  const active = FACTORS.filter((f) => w[f.id] > 0)
  const apply = (id: string) => { setPreset(id); setW(PRESETS.find((p) => p.id === id)!.w); setMust([]) }
  const toggleMust = (f: Factor) => setMust((m) => (m.includes(f) ? m.filter((x) => x !== f) : [...m, f]))
  const short = (f: Factor) => FACTORS.find((x) => x.id === f)!.short.toLowerCase()
  const hiddenTip = hidden.slice(0, 40).map((h) => `${h.area.name}: ${h.reasons.map((f) => (h.area && raw(h.area, f) == null ? `no ${short(f)} data` : `${short(f)} ${gradeOf(pool.find((r) => r.key === h.area.key)?.pctl[f])}`)).join(', ')}`).join('\n') + (hidden.length > 40 ? `\n…and ${hidden.length - 40} more` : '')
  const rangeOf = (s: Scored) => { const q = rb?.get(s.area.key); return q ? (q.lo === q.hi ? `${q.lo}` : `${q.lo}–${q.hi}`) : '' }
  const steady = (s: Scored) => { const q = rb?.get(s.area.key); return q ? q.hi - q.lo <= Math.max(3, Math.round(s.rank * 0.5)) : true }
  const sel = scored.find((s) => s.area.key === picked) ?? scored[0]
  const onSort = (key: string) => setSort((s) => ({ key, dir: s.key === key ? (s.dir === 1 ? -1 : 1) : 1 }))

  const tierBadge = (t: Tier, size = '') => <span className={`f-tier ${size}`} style={tierStyle(t, ramp)} aria-label={`${t} tier`}>{t}</span>
  const gradeBox = (s: Scored, f: Factor) => {
    const g = gradeOf(s.pctl[f])
    if (g == null) return <span className="f-nodata">no data</span>
    const label = FACTORS.find((x) => x.id === f)!.label
    return <span className={`g-sq ${g === 'E' ? 'e' : ''}`} style={gradeStyle(g, ramp)} title={`${label}: better than ${s.pctl[f]}% of ranked areas`}>{g}</span>
  }

  return (
    <div className="finder">
      <section className="card f-controls">
        <div className="f-row">
          <Segmented label="Goal" value={preset} onChange={apply} options={[...PRESETS.map((p) => ({ id: p.id, label: p.label })), ...(preset === 'custom' ? [{ id: 'custom', label: 'Custom' }] : [])]} />
          <div className="f-inputs">
            <label className="field f-inline"><span>Max price ($)</span>
              <input id="scr-budget" inputMode="numeric" value={budget} onChange={(e) => setBudget(e.target.value.replace(/[^\d]/g, ''))} placeholder="No limit" /></label>
            <label className="field f-inline"><span>Min sales / yr</span>
              <select id="scr-min" value={minSales} onChange={(e) => setMinSales(+e.target.value)}>{[12, 30, 60, 120, 250].map((n) => <option key={n} value={n}>{n}</option>)}</select></label>
          </div>
        </div>
        <div className="weights f-weights">
          {FACTORS.map((f) => (
            <div className="weight" key={f.id}>
              <label className="weight-top" htmlFor={`w-${f.id}`} title={f.hint}><b>{f.label}</b><span>{w[f.id] === 0 ? 'ignored' : `weight ${w[f.id]}`}</span></label>
              <div className="f-slide">
                <input id={`w-${f.id}`} type="range" min={0} max={5} step={1} value={w[f.id]} onChange={(e) => { const nw = { ...w, [f.id]: +e.target.value }; setW(nw); setPreset(presetFor(nw)) }} />
                <button className={`f-must ${must.includes(f.id) ? 'on' : ''}`} aria-pressed={must.includes(f.id)} onClick={() => toggleMust(f.id)}
                  title={`Must: hide areas graded D or E on ${f.label.toLowerCase()} (bottom ${WEAK_BELOW}%)`}>must</button>
              </div>
            </div>
          ))}
        </div>
        <p className="f-how">
          <span>Score = weighted geometric average of city ranks.</span>
          <details><summary>How scoring works</summary><span className="f-how-body">{HOW}</span></details>
          {hidden.length > 0 && <span className="f-hidden" title={hiddenTip}>{hidden.length} hidden by must-haves</span>}
        </p>
      </section>

      {scored.length > 0 && (
        <section className="card f-tierlist">
          <header className="card-head">
            <div><h3>Matches by tier</h3></div>
            {scored.length > 60 && <button className="ghost small" aria-expanded={allTiers} onClick={() => setAllTiers(!allTiers)}>{allTiers ? 'Show less' : 'Show all'}</button>}
          </header>
          <div className={`f-tiers ${scored.length > 60 && !allTiers ? 'capped' : ''}`}>
            {TIERS.map((t) => {
              const list = scored.filter((s) => s.tier === t)
              return (
                <div className="f-tier-row" key={t}>
                  {tierBadge(t, 'mid')}
                  <div className="f-chips">
                    {list.length === 0 && <span className="muted">none</span>}
                    {list.map((s) => (
                      <button key={s.area.key} className={`f-chip ${s.area.key === sel?.area.key ? 'on' : ''}`} onClick={() => { setPicked(s.area.key); requestAnimationFrame(() => detailRef.current?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })) }} title={`#${s.rank} · score ${s.score}`}>
                        {s.area.name}
                      </button>
                    ))}
                  </div>
                </div>
              )
            })}
          </div>
        </section>
      )}

      <div className="f-split">
        <section className="card">
          <MapView center={city.center} zoom={city.zoom} points={points} theme={theme} selected={sel?.area.key ?? null} onSelect={(id) => setPicked(String(id))} height={380} />
          <div className="map-legend">
            {TIERS.map((t) => <span key={t}><i className="key-box" style={{ background: ramp[TIER_SEQ[t]].bg }} />{t} tier</span>)}
          </div>
        </section>

        <section className="card f-detail" ref={detailRef}>
          {sel ? <>
            <header className="f-detail-head">
              {tierBadge(sel.tier, 'big')}
              <div className="f-detail-title">
                <h2>{sel.area.name}</h2>
                <div className="muted f-sub">
                  {sel.area.group && <span>{sel.area.group}</span>}
                  {sel.area.zips.length > 0 && <span className="f-zips">{sel.area.zips.join(' ')}</span>}
                </div>
              </div>
              <div className="f-detail-score"><b>{sel.score}</b><span>#{sel.rank} of {scored.length}</span></div>
            </header>
            {(() => {
              const known = active.filter((f) => sel.pctl[f.id] != null)
              const strong = [...known].sort((a, b) => (sel.pctl[b.id] as number) - (sel.pctl[a.id] as number)).slice(0, 2)
              const weak = [...known].sort((a, b) => (sel.pctl[a.id] as number) - (sel.pctl[b.id] as number))[0]
              const q = rb?.get(sel.area.key)
              return (
                <div className="f-why">
                  {strong.length > 0 && <span>Strongest: {strong.map((f) => `${f.short} ${gradeOf(sel.pctl[f.id])}`).join(', ')}</span>}
                  {weak && !strong.includes(weak) && <span>Held back by: {weak.short} {gradeOf(sel.pctl[weak.id])}</span>}
                  {sel.demoted && <span className="f-warn">Held out of S: weak on a heavily weighted factor</span>}
                  {sel.missing.length > 0 && <span className="f-warn">Incomplete: no {sel.missing.map(short).join(', ')} data, counted at the city median</span>}
                  <span className="muted" title="Rank across 200 small changes to your weights">{q ? `Top 10 in ${Math.round(q.top10 * 100)}% of nearby weightings · rank ${rangeOf(sel)}` : 'Checking nearby weightings…'}</span>
                </div>
              )
            })()}
            <div className="f-facts">
              {FACTORS.map((f) => {
                const on = w[f.id] > 0
                const v = value(sel.area, f.id)
                return (
                  <div className={`f-fact ${on ? '' : 'off'}`} key={f.id} title={f.hint}>
                    <span className="f-fact-label">{f.label}{!on && <em> not weighted</em>}</span>
                    <span className="f-fact-val" title={UNIT[f.id]}>{v ?? <span className="muted">no data</span>}</span>
                    {v == null ? <span /> : gradeBox(sel, f.id)}
                  </div>
                )
              })}
            </div>
            <MarketNow l={sel.area.listings} area />
            <a className="ghost small f-more" href={`#/${city.id}/areas`}>Open in Areas</a>
          </> : <p className="muted">No area selected.</p>}
        </section>
      </div>

      <section className="card f-rank">
        <header className="card-head"><div><h3>{scored.length} areas ranked</h3></div></header>
        {scored.length === 0 ? <p className="muted">Nothing fits. Raise the price limit, lower the sales minimum, or weight at least one factor.</p> : (
          <div className="table-scroll f-table">
            <table className="data compact">
              <thead><tr>
                <SortHead id="rank" label="#" sort={sort} onSort={onSort} />
                <SortHead id="name" label={city.areaNoun} sort={sort} onSort={onSort} />
                <SortHead id="score" label="Tier · score" sort={sort} onSort={onSort} />
                <th className="f-range-h" title="Rank across 200 small changes to your weights (5th–95th percentile)">Range</th>
                {active.map((f) => <SortHead key={f.id} id={f.id} label={f.short} sort={sort} onSort={onSort} num />)}
              </tr></thead>
              <tbody>
                {rows.map((s) => {
                  const sub = [s.area.group, s.area.zips.join(' ')].filter(Boolean).join(' · ')
                  return (
                    <tr key={s.area.key} className={`click ${s.area.key === sel?.area.key ? 'sel' : ''}`} onClick={() => setPicked(s.area.key)}>
                      <td className="rank">{s.rank}</td>
                      <td className="f-name" title={`${s.area.name}${sub ? ` · ${sub}` : ''}`}><b>{s.area.name}</b>{s.missing.length > 0 && <span className="f-inc" title={`Incomplete: no ${s.missing.map(short).join(', ')} data, counted at the city median`}>*</span>}{sub && <span className="muted"> {sub}</span>}</td>
                      <td><span className="f-ts">{tierBadge(s.tier)}<b>{s.score}</b><span className="mini"><i style={{ width: `${s.score}%` }} /></span></span></td>
                      <td className={`f-range ${steady(s) ? '' : 'shifts'}`} title="Rank across 200 small changes to your weights">{rangeOf(s)}</td>
                      {active.map((f) => {
                        const v = value(s.area, f.id)
                        return (
                          <td key={f.id} className="num" title={UNIT[f.id]}>
                            {v == null ? <span className="f-nodata">no data</span> : <span className="f-cell">{v}{gradeBox(s, f.id)}</span>}
                          </td>
                        )
                      })}
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <p className="footnote">
        A screen for research, not advice. Scores use only sale prices, rents, sales counts and police-reported incidents, nothing about who lives in an area.<br />
        Gross yield ignores taxes, insurance, repairs and vacancy, which run highest in the lowest-priced areas.
      </p>
    </div>
  )
}
