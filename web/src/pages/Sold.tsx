import '../styles/sold.css'
import { useMemo, useState } from 'react'
import { Area, Areas, City, useData } from '../lib/data'
import { Sold, SoldArea } from '../lib/areaTrends'
import { dateLabel, monthLabel, num, pct, usd, usdCompact } from '../lib/format'
import { Segmented } from '../components/bits'

type Key = 'n' | 'median' | 'ppsf' | 'medianYoY' | 'salesChange' | 'latestSale' | 'name'
type Pace = 'all' | 'up' | 'down'
const SORTS: { id: Key; label: string }[] = [
  { id: 'n', label: 'Most sold' }, { id: 'median', label: 'Typical price' }, { id: 'ppsf', label: 'Price per sq ft' },
  { id: 'medianYoY', label: 'Price change' }, { id: 'salesChange', label: 'Sales change' }, { id: 'latestSale', label: 'Latest sale' },
  { id: 'name', label: 'Name' },
]
interface Row extends SoldArea { area: Area }

const shortDate = (d: string | null) => (d ? dateLabel(d).replace(/, \d{4}$/, '') : '–')

export default function SoldPage({ city }: { city: City }) {
  const { data } = useData<Sold>(`${city.id}/sold.json`)
  const { data: areaData } = useData<Areas>(`${city.id}/areas.json`)
  const [sort, setSort] = useState<{ key: Key; dir: 1 | -1 }>({ key: 'n', dir: -1 })
  const [pace, setPace] = useState<Pace>('all')
  const [query, setQuery] = useState('')
  const [open, setOpen] = useState<string | null>(null)

  const all: Row[] = useMemo(() => {
    const byKey = new Map((areaData?.areas ?? []).map((a) => [a.key, a]))
    return (data?.areas ?? []).flatMap((s) => { const area = byKey.get(s.key); return area ? [{ ...s, area }] : [] })
  }, [data, areaData])

  const rows = useMemo(() => {
    const q = query.trim().toLowerCase()
    const val = (r: Row): number | string | null => sort.key === 'name' ? r.area.name : r[sort.key]
    return all
      .filter((r) => (pace === 'all' || r.pace === pace) && (!q || `${r.area.name} ${r.area.group} ${r.area.zips.join(' ')}`.toLowerCase().includes(q)))
      .sort((a, b) => {
        const [x, y] = [val(a), val(b)]
        if (x == null) return y == null ? 0 : 1
        if (y == null) return -1
        return (x < y ? -1 : x > y ? 1 : 0) * sort.dir
      })
  }, [all, sort, pace, query])

  if (!data || !areaData) return <div className="loading">Loading sold prices…</div>
  const up = all.filter((r) => r.pace === 'up').length
  const down = all.filter((r) => r.pace === 'down').length
  const period = `${monthLabel(data.from)} – ${monthLabel(data.through)}`
  const onSort = (key: Key) => setSort((s) => ({ key, dir: s.key === key ? (s.dir === 1 ? -1 : 1) : key === 'name' ? 1 : -1 }))
  const head = (key: Key, label: string, cls = '') => (
    <button className={`sold-h ${cls} ${sort.key === key ? 'on' : ''}`} onClick={() => onSort(key)} aria-sort={sort.key === key ? (sort.dir === 1 ? 'ascending' : 'descending') : 'none'}>
      {label}{sort.key === key ? (sort.dir === 1 ? ' ↑' : ' ↓') : ''}
    </button>
  )

  return (
    <>
      <p className="sold-lead">What homes sold for in each {city.areaNoun.toLowerCase()} over the last {data.months} complete months ({period}), against the same months a year earlier.</p>

      <div className="filters sold-filters">
        <Segmented label="Show" value={pace} onChange={setPace} options={[
          { id: 'all', label: `All ${all.length}` }, { id: 'up', label: `Selling more ${up}` }, { id: 'down', label: `Selling less ${down}` },
        ]} />
        <label className="field sold-sort"><span>Sort by</span>
          <select value={sort.key} onChange={(e) => { const key = e.target.value as Key; setSort({ key, dir: key === 'name' ? 1 : -1 }) }}>
            {SORTS.map((s) => <option key={s.id} value={s.id}>{s.label}</option>)}
          </select>
        </label>
        <label className="field sold-search"><span>Find</span>
          <input type="search" value={query} onChange={(e) => setQuery(e.target.value)} placeholder={`${city.areaNoun} or ZIP`} />
        </label>
      </div>

      <section className="card sold-card">
        <div className="sold-row sold-head" role="presentation">
          {head('name', city.areaNoun, 'c-name')}
          {head('median', 'Typical price', 'c-price')}
          <span className="sold-h c-range">Middle half</span>
          {head('ppsf', 'Per sq ft', 'c-ppsf')}
          {head('medianYoY', 'Vs year ago', 'c-yoy')}
          {head('n', `Sold (${data.months} mo)`, 'c-n')}
          {head('salesChange', 'Sales pace', 'c-pace')}
          {head('latestSale', 'Latest sale', 'c-latest')}
        </div>
        <ul className="sold-list">
          {rows.map((r) => {
            const isOpen = open === r.key
            const few = r.median == null
            return (
              <li key={r.key} className={`sold-item ${isOpen ? 'open' : ''}`}>
                <button className="sold-row" aria-expanded={isOpen} onClick={() => setOpen(isOpen ? null : r.key)}>
                  <span className="c-name"><b>{r.area.name}</b>{r.area.group && r.area.group !== r.area.name && <small className="muted"> {r.area.group}</small>}</span>
                  <span className="c-price">{few ? <span className="muted">Too few sales</span> : <b>{usd(Math.round(r.median! / 1000) * 1000)}</b>}</span>
                  <span className="c-range"><small className="k">Middle half </small>{few ? '–' : `${usdCompact(r.p25)} – ${usdCompact(r.p75)}`}</span>
                  <span className="c-ppsf"><small className="k">Per sq ft </small>{r.ppsf == null ? '–' : usd(r.ppsf)}</span>
                  <span className="c-yoy">{r.medianYoY == null ? <span className="muted">–</span> : <>{r.medianYoY > 0 ? '▲' : r.medianYoY < 0 ? '▼' : '■'} {pct(Math.abs(r.medianYoY)).replace('+', '')}<small className="k"> vs year ago</small></>}</span>
                  <span className="c-n">{num(r.n)}<small className="k"> sold</small><small className="muted"> {r.salesChange == null ? '' : `(${pct(r.salesChange, 0)})`}</small></span>
                  <span className="c-pace">{r.pace === 'up' ? <i className="pace up">Selling more</i> : r.pace === 'down' ? <i className="pace down">Selling less</i> : <span className="muted">{r.pace === 'flat' ? 'Steady' : '–'}</span>}</span>
                  <span className="c-latest"><small className="k">Latest </small>{shortDate(r.latestSale)}</span>
                </button>
                {isOpen && (
                  <div className="sold-detail">
                    <ul>
                      {r.latest.map((s, i) => (
                        <li key={i}>
                          <span className="d-date">{dateLabel(s.date)}</span>
                          <span className="d-addr">{s.address || <span className="muted">Address not published</span>} <small className="muted">{s.type}</small></span>
                          <b className="d-price">{usd(s.price)}</b>
                          <span className="d-ppsf muted">{s.sqft ? `${usd(s.price / s.sqft)}/sq ft` : ''}</span>
                        </li>
                      ))}
                    </ul>
                    <a className="ghost small" href={`#/${city.id}/market?area=${encodeURIComponent(r.key)}`}>Price trend for {r.area.name}</a>
                  </div>
                )}
              </li>
            )
          })}
          {rows.length === 0 && <li className="muted sold-empty">No {city.areaNoun.toLowerCase()} matches.</li>}
        </ul>
      </section>
      <p className="footnote">
        Recorded sales only, never asking prices. Typical price is the median: half sold for more, half for less. Middle half is the range the middle 50% of sales fell in.
        “Too few sales” means under 10 in the period. Selling more or less compares the number sold with the same months a year earlier and shows only when the change is unlikely to be chance.
        Months still being filled in with new records are left out; the latest individual sales may be newer.
      </p>
    </>
  )
}
