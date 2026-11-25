import '../styles/market.css'
import { useMemo, useState } from 'react'
import { Areas, City, Overview, useData } from '../lib/data'
import { AreaSeries, hashParam, setHashParam } from '../lib/areaTrends'
import { compact, monthLabel, num, pct, usd, usdCompact } from '../lib/format'
import ChartCard from '../charts/ChartCard'
import LineChart, { LineSeries } from '../charts/LineChart'
import ColumnChart from '../charts/ColumnChart'
import Sparkline from '../charts/Sparkline'
import { yearTicks } from '../charts/scale'
import { Delta, placeTip, Segmented, StatTile, TYPE_COLOR } from '../components/bits'

type Range = '3y' | '5y' | 'all'
const RANGE_MONTHS: Record<Range, number> = { '3y': 36, '5y': 60, all: 10000 }
/** Citywide repeat-sales index, one value per 12-month period (years = each period's last month). */
interface RepeatIndex { years: string[]; index: (number | null)[]; halfPairs: number[]; pairs: number; growth1: number | null; growth5: number | null }

export default function Market({ city }: { city: City }) {
  const { data } = useData<Overview & { repeatIndex?: RepeatIndex | null }>(`${city.id}/overview.json`)
  const { data: areas } = useData<Areas>(`${city.id}/areas.json`)
  const [type, setType] = useState('All homes')
  const [range, setRange] = useState<Range>('5y')
  const [compare, setCompare] = useState(false)
  const [areaKey, setAreaKey] = useState(hashParam('area'))
  const { data: series } = useData<AreaSeries>(areaKey ? `${city.id}/area-series.json` : null)
  const pick = (key: string) => { setAreaKey(key); setHashParam('area', key) }

  const movers = useMemo(() => {
    const ranked = (areas?.areas ?? []).filter((a) => a.sales12 >= 40 && a.medianYoY != null)
      .sort((a, b) => (b.medianYoY as number) - (a.medianYoY as number))
    return { up: ranked.slice(0, 5), down: ranked.slice(-5).reverse() }
  }, [areas])

  const area = areas?.areas.find((a) => a.key === areaKey)
  const picker = areas && (
    <label className="field market-area"><span>Area</span>
      <select value={area ? areaKey : ''} onChange={(e) => pick(e.target.value)}>
        <option value="">All of {city.name}</option>
        {[...areas.areas].sort((x, y) => x.name.localeCompare(y.name)).map((a) => (
          <option key={a.key} value={a.key}>{a.name}{a.group && a.group !== a.name ? ` (${a.group})` : ''}</option>
        ))}
      </select>
    </label>
  )
  if (!data) return <div className="loading">Loading {city.name}…</div>
  if (area) {
    if (!series) return <>{picker}<div className="loading">Loading {area.name}…</div></>
    const s = series.areas[area.key]
    if (!s) return <>{picker}<p className="muted">No monthly figures for {area.name} yet.</p></>
    return <AreaView picker={picker} name={area.name} series={series} s={s} range={range === 'all' ? '5y' : range} setRange={setRange} />
  }

  const t = data.types[type] ?? data.types['All homes']
  const start = Math.max(0, data.months.length - RANGE_MONTHS[range])
  const months = data.months.slice(start)
  const labels = months.map(monthLabel)
  const cut = <T,>(a: T[]) => a.slice(start)
  const ticks = (w: number) => yearTicks(months, w)
  const k = t.kpi
  const last = t.series.median.length - 1
  const homeTypes = data.typeOrder.filter((n) => n !== 'All homes')

  const priceSeries: LineSeries[] = compare
    ? homeTypes.map((n) => ({ name: n, color: TYPE_COLOR[n], values: cut(data.types[n].series.median) }))
    : [{
        name: 'Median sale price', color: TYPE_COLOR[type], values: cut(t.series.median),
        band: { lo: cut(t.series.p25), hi: cut(t.series.p75), label: 'Middle half of sales' },
      }]
  const ppsfSeries: LineSeries[] = compare
    ? homeTypes.filter((n) => data.types[n].series.ppsf.some((v) => v != null))
        .map((n) => ({ name: n, color: TYPE_COLOR[n], values: cut(data.types[n].series.ppsf) }))
    : [{ name: 'Median price per sq ft', color: TYPE_COLOR[type], values: cut(t.series.ppsf) }]
  const hasPpsf = ppsfSeries.some((s) => s.values.some((v) => v != null))
  const hist = t.hist
  const ri = data.repeatIndex
  const riLabels = ri ? ri.years.map((m) => `12 months to ${monthLabel(m)}`) : []
  const histLabels = bandLabels(hist.edges, hist.counts.length)

  return (
    <>
      <div className="filters market-filters">
        {picker}
        <Segmented label="Home type" value={type} onChange={(v) => { setType(v); setCompare(false) }}
          options={data.typeOrder.map((n) => ({ id: n, label: n }))} />
        <Segmented label="Time range" value={range} onChange={setRange}
          options={[{ id: '3y', label: '3 years' }, { id: '5y', label: '5 years' }, { id: 'all', label: 'All' }]} />
        {homeTypes.length > 1 && (
          <label className="check"><input type="checkbox" checked={compare} onChange={(e) => setCompare(e.target.checked)} /> Compare home types</label>
        )}
      </div>

      <div className="tiles market-tiles">
        <StatTile hero label={`Median sale price · ${type.toLowerCase()}`} value={usd(k.median)}>
          <Delta value={k.medianYoY} suffix="vs a year earlier" />
          <span className="muted"> · 3 months to {monthLabel(data.through)}</span>
        </StatTile>
        <StatTile label="Price per sq ft" value={k.ppsf == null ? 'Not published' : usd(k.ppsf)}>
          {k.ppsf == null ? <span className="muted">The source has no floor area for these homes</span> : <Delta value={k.ppsfYoY} suffix="vs a year earlier" />}
        </StatTile>
        <StatTile label="Homes sold, last 12 months" value={num(k.sales12)}>
          <Delta value={k.salesYoY} suffix="vs the 12 months before" />
        </StatTile>
        <StatTile label={`Middle half of sales, ${monthLabel(data.through)}`} value={t.series.p25[last] == null ? '–' : `${usdCompact(t.series.p25[last])} – ${usdCompact(t.series.p75[last])}`}>
          <span className="muted">A quarter of homes sold below this range, a quarter above</span>
        </StatTile>
      </div>

      {data.listings && (() => {
        const l = data.listings
        const foot = `Listings, ${monthLabel(l.month)}, vs a year earlier`
        return (
          <div className="tiles market-tiles">
            <StatTile label="Homes for sale" value={num(l.active)}><Delta value={l.activeYoY} short /> <span className="muted">{foot}</span></StatTile>
            <StatTile label="Newly listed" value={num(l.newListings)}><Delta value={l.newListingsYoY} short /> <span className="muted">{foot}</span></StatTile>
            <StatTile label="Median asking price" value={usdCompact(l.listPrice)}><Delta value={l.listPriceYoY} short /> <span className="muted">Asking, not sale prices</span></StatTile>
            <StatTile label="Listings with a price cut" value={l.priceCutShare == null ? '–' : `${l.priceCutShare.toFixed(0)}%`}><Delta value={l.priceCutShareYoY} points short /> <span className="muted">{foot}</span></StatTile>
          </div>
        )
      })()}

      <div className="grid-2">
        <ChartCard className="span-2" title={compare ? 'Median sale price by home type' : 'Median sale price'}
          subtitle={compare ? 'Monthly median of recorded sales' : `Monthly median for ${type.toLowerCase()}. The shaded range holds the middle half of sales.`}
          table={{ head: ['Month', ...priceSeries.map((s) => s.name)], rows: labels.map((l, i) => [l, ...priceSeries.map((s) => usd(s.values[i]))]).reverse() }}>
          <LineChart labels={labels} ticks={ticks} series={priceSeries} format={usd} axisFormat={usdCompact} height={320} />
        </ChartCard>

        <ChartCard title="Homes sold per month" subtitle={`Recorded sales of ${type.toLowerCase()}`}
          table={{ head: ['Month', 'Homes sold'], rows: labels.map((l, i) => [l, num(cut(t.series.count)[i])]).reverse() }}>
          <ColumnChart labels={labels} values={cut(t.series.count)} ticks={ticks} format={compact} unit="Homes sold" color={TYPE_COLOR[type]} />
        </ChartCard>

        <HistCard edges={hist.edges} counts={hist.counts} labels={histLabels} what={type.toLowerCase()} color={TYPE_COLOR[type]} />

        {hasPpsf && (
          <ChartCard className="span-2" title="Price per square foot" subtitle="Monthly median, for homes whose floor area is on record"
            table={{ head: ['Month', ...ppsfSeries.map((s) => s.name)], rows: labels.map((l, i) => [l, ...ppsfSeries.map((s) => usd(s.values[i]))]).reverse() }}>
            <LineChart labels={labels} ticks={ticks} series={ppsfSeries} format={usd} height={240} />
          </ChartCard>
        )}

        {ri && ri.index.some((v) => v != null) && (
          <ChartCard className="span-2" title="Repeat-sales price index"
            subtitle={`Tracks the same homes selling twice, so it is not affected by which homes happened to sell. First year = 100. ${num(ri.pairs)} pairs.`}
            table={{ head: ['Period', 'Index'], rows: riLabels.map((l, i) => [l, ri.index[i] == null ? '–' : ri.index[i]!.toFixed(1)]).reverse() }}>
            <LineChart labels={riLabels} series={[{ name: 'Repeat-sales index', color: 'var(--series-1)', values: ri.index }]}
              ticks={() => ri.years.map((m, index) => ({ index, label: m.slice(0, 4) }))}
              format={(v) => v.toFixed(1)} axisFormat={(v) => v.toFixed(0)} height={240} />
          </ChartCard>
        )}

        {areas && movers.up.length > 0 && (
          <>
            <MoverCard title="Prices rising fastest" city={city} rows={movers.up} noun={city.areaNoun} onPick={pick} />
            <MoverCard title="Prices falling or flattest" city={city} rows={movers.down} noun={city.areaNoun} onPick={pick} />
          </>
        )}
      </div>
      <p className="footnote">{city.notes} Medians mix every home that sold in the period, so a change can reflect a different mix of homes as well as a change in values.</p>
    </>
  )
}

const bandLabels = (edges: number[], n: number) =>
  Array.from({ length: n }, (_, i) => i === n - 1 ? `${usdCompact(edges[i])} and up` : `${usdCompact(edges[i])} – ${usdCompact(edges[i + 1])}`)

function HistCard({ edges, counts, labels, what, color, warn }: { edges: number[]; counts: number[]; labels: string[]; what: string; color: string; warn?: boolean }) {
  return (
    <ChartCard title={<>Sale prices, last 12 months <Few on={!!warn} /></>} subtitle={`How many ${what} sold in each price band`}
      table={{ head: ['Price band', 'Homes sold'], rows: labels.map((l, i) => [l, num(counts[i])]) }}>
      <ColumnChart labels={labels} values={counts} tickAlign="edge" format={compact} unit="Homes sold" color={color}
        ticks={(w) => edges.map((e, index) => ({ index, label: usdCompact(e) })).filter((_, i) => i % (w < 420 ? 5 : 3) === 0)} />
    </ChartCard>
  )
}

/** One area: recorded sales only. Listing figures are published by ZIP code, so they are left out here. */
const FEW_MSG = 'Too few sales — data might be incomplete or inaccurate'

/** Small warning marker; hover or tap (focus) shows the message. */
function Few({ on = true }: { on?: boolean }) {
  if (!on) return null
  return <button type="button" className="few-mark" aria-label={FEW_MSG} data-tip={FEW_MSG} onFocus={placeTip} onPointerEnter={placeTip} onPointerDown={placeTip}>!</button>
}

/** One area: recorded sales only. Every figure is shown; those resting on few sales carry a warning marker. */
function AreaView({ picker, name, series, s, range, setRange }: {
  picker: React.ReactNode; name: string; series: AreaSeries; s: AreaSeries['areas'][string]; range: Range; setRange: (r: Range) => void
}) {
  const start = Math.max(0, series.months.length - RANGE_MONTHS[range])
  const months = series.months.slice(start)
  const labels = months.map(monthLabel)
  const ticks = (w: number) => yearTicks(months, w)
  const k = s.kpi
  const f = k.few
  const color = TYPE_COLOR['All homes']
  const edges = Array.from({ length: s.hist.counts.length + 1 }, (_, i) => i * s.hist.step)
  const median = s.median.slice(start)
  const medianN = s.medianN.slice(start)
  const ppsf = s.ppsf.slice(start)
  const ppsfN = s.ppsfN.slice(start)
  const count = s.count.slice(start)
  const fewMed = median.map((v, i) => v != null && medianN[i] < series.fewBelow)
  const fewPpsf = ppsf.map((v, i) => v != null && ppsfN[i] < series.fewBelow)
  return (
    <>
      <div className="filters market-filters">
        {picker}
        <Segmented label="Time range" value={range} onChange={setRange}
          options={[{ id: '3y', label: '3 years' }, { id: '5y', label: '5 years' }]} />
        <span className="muted market-note">All home types · sold prices only</span>
      </div>

      <div className="tiles market-tiles">
        <div className="few-wrap hero"><Few on={f.median || (k.medianYoY != null && f.medianYoY)} />
          <StatTile hero label={`Median sale price · ${name}`} value={usd(k.median12)}>
            <Delta value={k.medianYoY} suffix="vs a year earlier" /><span className="muted"> · 12 months to {monthLabel(series.through)}</span>
          </StatTile>
        </div>
        <div className="few-wrap"><Few on={k.ppsf12 != null && (f.ppsf || (k.ppsfYoY != null && f.ppsfYoY))} />
          <StatTile label="Price per sq ft" value={k.ppsf12 == null ? 'Not published' : usd(k.ppsf12)}>
            {k.ppsf12 == null ? <span className="muted">No floor area on record</span> : <Delta value={k.ppsfYoY} suffix="vs a year earlier" />}
          </StatTile>
        </div>
        <div className="few-wrap"><Few on={k.salesYoY != null && f.salesYoY} />
          <StatTile label="Homes sold, 12 months" value={num(k.sales12)}>
            <Delta value={k.salesYoY} suffix="vs the year before" />
          </StatTile>
        </div>
        {k.p25 != null && (
          <div className="few-wrap"><Few on={f.range} />
            <StatTile label="Middle half of sales" value={`${usdCompact(k.p25)} – ${usdCompact(k.p75)}`}>
              <span className="muted">Last 12 months</span>
            </StatTile>
          </div>
        )}
      </div>
      <p className="muted market-note">Listing figures: city-wide only.</p>

      <div className="grid-2">
        {median.some((v) => v != null) && (
          <ChartCard className="span-2" title={<>Median sale price <Few on={fewMed.some(Boolean)} /></>}
            subtitle={`${series.rollMonths}-month rolling median`}
            table={{ head: ['Month', 'Median sale price', 'Sales'], rows: labels.map((l, i) => [l, usd(median[i]), num(medianN[i])]).reverse() }}>
            <LineChart labels={labels} ticks={ticks} series={[{ name: 'Median sale price', color, values: median, few: fewMed }]}
              format={usd} axisFormat={usdCompact} height={300} />
          </ChartCard>
        )}
        <ChartCard title="Homes sold per month" subtitle={name}
          table={{ head: ['Month', 'Homes sold'], rows: labels.map((l, i) => [l, num(count[i])]).reverse() }}>
          <ColumnChart labels={labels} values={count} ticks={ticks} format={compact} unit="Homes sold" color={color} />
        </ChartCard>
        {k.sales12 > 0 && <HistCard edges={edges} counts={s.hist.counts} labels={bandLabels(edges, s.hist.counts.length)} what="homes" color={color} warn={f.range} />}
        {ppsf.some((v) => v != null) && (
          <ChartCard className="span-2" title={<>Price per sq ft <Few on={fewPpsf.some(Boolean)} /></>}
            subtitle={`${series.rollMonths}-month rolling median, homes with floor area on record`}
            table={{ head: ['Month', 'Price per sq ft', 'Sales'], rows: labels.map((l, i) => [l, usd(ppsf[i]), num(ppsfN[i])]).reverse() }}>
            <LineChart labels={labels} ticks={ticks} series={[{ name: 'Median price per sq ft', color, values: ppsf, few: fewPpsf }]}
              format={(v) => usd(v)} height={240} />
          </ChartCard>
        )}
      </div>
      <p className="footnote">Hollow yellow points rest on under {series.fewBelow} sales. Medians mix every home sold, so they move with the mix as well as with values.</p>
    </>
  )
}

function MoverCard({ title, rows, city, noun, onPick }: { title: string; rows: Areas['areas']; city: City; noun: string; onPick: (key: string) => void }) {
  return (
    <section className="card">
      <header className="card-head">
        <div><h3>{title}</h3><p>Median price, last 12 months vs the 12 before. Areas with 40+ sales.</p></div>
        <a className="ghost small" href={`#/${city.id}/areas`}>All areas</a>
      </header>
      <table className="data compact movers">
        <thead><tr><th>{noun}</th><th className="num">Median</th><th className="num">1-yr change</th><th>6-year trend</th></tr></thead>
        <tbody>
          {rows.map((a) => (
            <tr key={a.key}>
              <td><button className="link-btn" onClick={() => { onPick(a.key); window.scrollTo(0, 0) }}><b>{a.name}</b></button>{a.group && <span className="muted"> {a.group}</span>}</td>
              <td className="num">{usd(a.median12)}</td>
              <td className="num">{pct(a.medianYoY)}</td>
              <td><Sparkline values={a.quarterly} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  )
}
