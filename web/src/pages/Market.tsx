import { useMemo, useState } from 'react'
import { Areas, City, Overview, useData } from '../lib/data'
import { compact, monthLabel, num, pct, usd, usdCompact } from '../lib/format'
import ChartCard from '../charts/ChartCard'
import LineChart, { LineSeries } from '../charts/LineChart'
import ColumnChart from '../charts/ColumnChart'
import Sparkline from '../charts/Sparkline'
import { yearTicks } from '../charts/scale'
import { Delta, Segmented, StatTile, TYPE_COLOR } from '../components/bits'
import MarketNow from '../components/MarketNow'

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

  const movers = useMemo(() => {
    const ranked = (areas?.areas ?? []).filter((a) => a.sales12 >= 40 && a.medianYoY != null)
      .sort((a, b) => (b.medianYoY as number) - (a.medianYoY as number))
    return { up: ranked.slice(0, 5), down: ranked.slice(-5).reverse() }
  }, [areas])

  if (!data) return <div className="loading">Loading {city.name}…</div>
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
  const histLabels = hist.counts.map((_, i) => i === hist.counts.length - 1 ? `${usdCompact(hist.edges[i])} and up` : `${usdCompact(hist.edges[i])} – ${usdCompact(hist.edges[i + 1])}`)

  return (
    <>
      <div className="filters" style={{ marginBottom: 0 }}>
        <Segmented label="Home type" value={type} onChange={(v) => { setType(v); setCompare(false) }}
          options={data.typeOrder.map((n) => ({ id: n, label: n }))} />
        <Segmented label="Time range" value={range} onChange={setRange}
          options={[{ id: '3y', label: '3 years' }, { id: '5y', label: '5 years' }, { id: 'all', label: 'All' }]} />
        {homeTypes.length > 1 && (
          <label className="check"><input type="checkbox" checked={compare} onChange={(e) => setCompare(e.target.checked)} /> Compare home types</label>
        )}
      </div>

      <div className="tiles">
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

      <MarketNow l={data.listings} wide />

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

        <ChartCard title="Sale prices, last 12 months" subtitle={`How many ${type.toLowerCase()} sold in each price band`}
          table={{ head: ['Price band', 'Homes sold'], rows: histLabels.map((l, i) => [l, num(hist.counts[i])]) }}>
          <ColumnChart labels={histLabels} values={hist.counts} tickAlign="edge" format={compact} unit="Homes sold" color={TYPE_COLOR[type]}
            ticks={(w) => hist.edges.map((e, index) => ({ index, label: usdCompact(e) })).filter((_, i) => i % (w < 420 ? 5 : 3) === 0)} />
        </ChartCard>

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
            <MoverCard title="Prices rising fastest" city={city} rows={movers.up} noun={city.areaNoun} />
            <MoverCard title="Prices falling or flattest" city={city} rows={movers.down} noun={city.areaNoun} />
          </>
        )}
      </div>
      <p className="footnote">{city.notes} Medians mix every home that sold in the period, so a change can reflect a different mix of homes as well as a change in values.</p>
    </>
  )
}

function MoverCard({ title, rows, city, noun }: { title: string; rows: Areas['areas']; city: City; noun: string }) {
  return (
    <section className="card">
      <header className="card-head">
        <div><h3>{title}</h3><p>Median price, last 12 months vs the 12 before. Areas with 40+ sales.</p></div>
        <a className="ghost small" href={`#/${city.id}/areas`}>All areas</a>
      </header>
      <table className="data compact">
        <thead><tr><th>{noun}</th><th className="num">Median</th><th className="num">1-yr change</th><th>6-year trend</th></tr></thead>
        <tbody>
          {rows.map((a) => (
            <tr key={a.key}>
              <td><b>{a.name}</b>{a.group && <span className="muted"> {a.group}</span>}</td>
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
