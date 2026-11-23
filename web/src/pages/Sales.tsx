import { useMemo, useState } from 'react'
import { Areas, City, Sales, useData } from '../lib/data'
import { Theme, cssVar } from '../lib/hooks'
import { dateLabel, num, pct, usd, usdCompact } from '../lib/format'
import MapView, { classOf, MapPoint, quintiles } from '../components/MapView'
import { SortHead } from '../components/bits'

type SortKey = 'date' | 'price' | 'sqft' | 'ppsf' | 'vs' | 'ratio'
const PAGE = 50
const MAP_CAP = 2500

export default function SalesPage({ city, theme }: { city: City; theme: Theme }) {
  const { data } = useData<Sales>(`${city.id}/sales.json`)
  const { data: areaData } = useData<Areas>(`${city.id}/areas.json`)
  const [type, setType] = useState('')
  const [area, setArea] = useState('')
  const [min, setMin] = useState('')
  const [max, setMax] = useState('')
  const [query, setQuery] = useState('')
  const [below, setBelow] = useState(false)
  const [sort, setSort] = useState<{ key: SortKey; dir: 1 | -1 }>({ key: 'date', dir: -1 })
  const [page, setPage] = useState(0)

  const c = data?.columns
  const areas = areaData?.areas ?? []
  const hasRatio = !!c?.ratio

  const rows = useMemo(() => {
    if (!c) return []
    const q = query.trim().toLowerCase()
    const lo = parseFloat(min) || 0
    const hi = parseFloat(max) || Infinity
    const areaIdx = area === '' ? null : +area
    const out: number[] = []
    for (let i = 0; i < c.date.length; i++) {
      if (type !== '' && c.type[i] !== +type) continue
      if (areaIdx != null && c.area[i] !== areaIdx) continue
      if (c.price[i] < lo || c.price[i] > hi) continue
      if (below && !(c.vs[i] != null && (c.vs[i] as number) <= -15)) continue
      if (q && !`${c.address[i]} ${c.zip[i]}`.toLowerCase().includes(q)) continue
      out.push(i)
    }
    const val = (i: number): number | string | null =>
      sort.key === 'date' ? c.date[i] : sort.key === 'price' ? c.price[i] : sort.key === 'sqft' ? c.sqft[i]
        : sort.key === 'ppsf' ? (c.sqft[i] ? c.price[i] / (c.sqft[i] as number) : null) : sort.key === 'vs' ? c.vs[i] : c.ratio?.[i] ?? null
    return out.sort((a, b) => {
      const [x, y] = [val(a), val(b)]
      if (x == null) return 1
      if (y == null) return -1
      return (x < y ? -1 : x > y ? 1 : 0) * sort.dir
    })
  }, [c, type, area, min, max, query, below, sort])

  const bounds = useMemo(() => (c ? quintiles(c.price) : []), [c])
  const points: MapPoint[] = useMemo(() => {
    if (!c) return []
    const out: MapPoint[] = []
    for (const i of rows) {
      if (c.lat[i] == null) continue
      out.push({
        id: i, lat: c.lat[i] as number, lon: c.lon[i] as number, radius: 4.5,
        color: cssVar(`--seq-${classOf(c.price[i], bounds) + 1}`),
        tip: `${c.address[i] || 'Address not published'}: ${usd(c.price[i])}, ${dateLabel(c.date[i])}`,
      })
      if (out.length >= MAP_CAP) break
    }
    return out
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rows, bounds, theme])

  if (!data || !c) return <div className="loading">Loading recent sales…</div>
  const onSort = (key: SortKey) => { setSort((s) => ({ key, dir: s.key === key ? (s.dir === 1 ? -1 : 1) : -1 })); setPage(0) }
  const set = <T,>(fn: (v: T) => void) => (v: T) => { fn(v); setPage(0) }
  const pages = Math.max(1, Math.ceil(rows.length / PAGE))
  const shown = rows.slice(page * PAGE, page * PAGE + PAGE)
  const median = rows.length ? [...rows].map((i) => c.price[i]).sort((a, b) => a - b)[Math.floor(rows.length / 2)] : null

  return (
    <>
      <section className="card">
        <div className="filters">
          <label className="field"><span>Home type</span>
            <select id="f-type" value={type} onChange={(e) => set(setType)(e.target.value)}>
              <option value="">All types</option>
              {data.typeNames.map((n, i) => c.type.includes(i) && <option key={n} value={i}>{n}</option>)}
            </select></label>
          <label className="field"><span>{city.areaNoun}</span>
            <select id="f-area" value={area} onChange={(e) => set(setArea)(e.target.value)} style={{ maxWidth: 240 }}>
              <option value="">All areas</option>
              {areas.map((a, i) => ({ a, i })).sort((x, y) => x.a.name.localeCompare(y.a.name)).map(({ a, i }) => (
                <option key={a.key} value={i}>{a.name}{a.group ? ` (${a.group})` : ''}</option>
              ))}
            </select></label>
          <label className="field"><span>Min price ($)</span><input id="f-min" inputMode="numeric" value={min} onChange={(e) => set(setMin)(e.target.value.replace(/[^\d]/g, ''))} placeholder="Any" style={{ width: 110 }} /></label>
          <label className="field"><span>Max price ($)</span><input id="f-max" inputMode="numeric" value={max} onChange={(e) => set(setMax)(e.target.value.replace(/[^\d]/g, ''))} placeholder="Any" style={{ width: 110 }} /></label>
          <label className="field"><span>Address or ZIP</span><input id="f-q" type="search" value={query} onChange={(e) => set(setQuery)(e.target.value)} placeholder="Search" /></label>
          <label className="check" title="Sold at least 15% below the typical price for the same home type in the same area">
            <input type="checkbox" checked={below} onChange={(e) => set(setBelow)(e.target.checked)} /> Sold 15%+ below area typical
          </label>
        </div>
      </section>

      <section className="card">
        <header className="card-head">
          <div><h3>{num(rows.length)} sales · median {usd(median)}</h3>
            <p>Recorded between {data.from ? dateLabel(data.from) : '–'} and {dateLabel(data.latestSale)}. Colour shows price; {points.length < rows.length ? `the map shows the first ${num(points.length)} with a known location.` : 'every match with a known location is on the map.'}</p></div>
        </header>
        <MapView center={city.center} zoom={city.zoom} points={points} theme={theme} height={380} />
        <div className="map-legend">
          {bounds.map((b, i) => (
            <span key={i}><i className="key-box" style={{ background: `var(--seq-${i + 1})` }} />{i === 0 ? 'up to' : `${usdCompact(bounds[i - 1])} –`} {usdCompact(b)}</span>
          ))}
        </div>
      </section>

      <section className="card">
        <div className="table-scroll">
          <table className="data">
            <thead><tr>
              <SortHead id="date" label="Sold" sort={sort} onSort={onSort} />
              <th>Address</th><th>{city.areaNoun}</th><th>Type</th>
              <SortHead id="price" label="Price" sort={sort} onSort={onSort} num />
              <SortHead id="sqft" label="Sq ft" sort={sort} onSort={onSort} num />
              <SortHead id="ppsf" label="Per sq ft" sort={sort} onSort={onSort} num />
              <SortHead id="vs" label="Vs area typical" sort={sort} onSort={onSort} num />
              {hasRatio && <SortHead id="ratio" label="Price / assessed" sort={sort} onSort={onSort} num />}
              <th className="num">Built</th>
            </tr></thead>
            <tbody>
              {shown.map((i) => (
                <tr key={i}>
                  <td>{dateLabel(c.date[i])}</td>
                  <td>{c.address[i] || <span className="muted">Not published</span>}</td>
                  <td>{c.area[i] >= 0 && areas[c.area[i]] ? areas[c.area[i]].name : <span className="muted">{c.zip[i] || '–'}</span>}</td>
                  <td>{data.typeNames[c.type[i]]}</td>
                  <td className="num"><b>{usd(c.price[i])}</b></td>
                  <td className="num">{num(c.sqft[i])}</td>
                  <td className="num">{c.sqft[i] ? usd(c.price[i] / (c.sqft[i] as number)) : '–'}</td>
                  <td className="num">{c.vs[i] == null ? '–' : pct(c.vs[i], 0)}</td>
                  {hasRatio && <td className="num">{c.ratio![i] == null ? '–' : `${c.ratio![i]}%`}</td>}
                  <td className="num">{c.yb[i] ?? '–'}</td>
                </tr>
              ))}
              {rows.length === 0 && <tr><td colSpan={10} className="muted">No sales match these filters. Widen the price range or clear the search.</td></tr>}
            </tbody>
          </table>
        </div>
        <div className="pager">
          <span>Page {page + 1} of {num(pages)}</span>
          <span style={{ display: 'flex', gap: 8 }}>
            <button className="ghost small" disabled={page === 0} onClick={() => setPage(page - 1)}>Previous</button>
            <button className="ghost small" disabled={page >= pages - 1} onClick={() => setPage(page + 1)}>Next</button>
          </span>
        </div>
      </section>
      <p className="footnote">“Vs area typical” compares a sale with the 12-month median for the same home type in the same area, per square foot where floor area is on record and by price otherwise. It is a screening signal, not a valuation: condition, lot and unit size all move a price. {hasRatio && 'Price / assessed divides the sale price by the city’s current assessed market value.'}</p>
    </>
  )
}
