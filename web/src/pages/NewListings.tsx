import { useState } from 'react'
import { City, useData } from '../lib/data'
import { dateLabel, usd } from '../lib/format'

type Snapshot = { status: 'unavailable' } | {
  status: 'ready'
  asOf: string
  pages: number
  partial: boolean
  rows: { url: string; address: string; date: string; price: number }[]
}
const portals: Record<string, string> = {
  philadelphia: '15502/PA/Philadelphia', chicago: '29470/IL/Chicago', nyc: '30749/NY/New-York',
}
const periods = ['Today', 'Yesterday', 'Last 7 days'] as const
type Period = typeof periods[number]

export default function NewListings({ city }: { city: City }) {
  const { data, error } = useData<Snapshot>(`${city.id}/new-listings.json`)
  const [period, setPeriod] = useState<Period>('Last 7 days')
  const timeZone = city.id === 'chicago' ? 'America/Chicago' : 'America/New_York'
  const today = new Intl.DateTimeFormat('sv-SE', { timeZone }).format(new Date())
  // Shift a calendar date, so a daylight-saving transition still counts as one day.
  const daysAgo = (days: number) => new Date(Date.parse(today) - days * 86400000).toISOString().slice(0, 10)
  const from = daysAgo(period === 'Today' ? 0 : period === 'Yesterday' ? 1 : 6)
  const through = period === 'Yesterday' ? from : today
  const rows = data?.status === 'ready' ? data.rows.filter(r => r.date >= from && r.date <= through) : []
  return <section className="card">
    <header className="card-head"><div>
      <h3>Newly listed homes for sale</h3>
      {data?.status === 'ready'
        ? <p>Asking prices from Trulia’s new listings, checked {new Date(data.asOf).toLocaleString('en-US', { timeZone, timeZoneName: 'short' })}. {data.partial ? `Partial results from ${data.pages} search pages.` : 'Updated with each daily site build.'}</p>
        : <p>{error || data ? 'The newest listings could not be loaded.' : 'Loading listings…'}</p>}
      <p><a href={`https://www.redfin.com/city/${portals[city.id]}/newest-listings`}>Browse the newest listings on Redfin ↗</a></p>
    </div></header>
    {data?.status === 'ready' && <>
      <div className="filters"><div className="segmented" role="group" aria-label="Date listed">
        {periods.map(p => <button key={p} className={p === period ? 'on' : ''} aria-pressed={p === period} onClick={() => setPeriod(p)}>{p}</button>)}
      </div></div>
      {rows.length ? <div className="table-scroll"><table className="data">
        <thead><tr><th>Listed</th><th>Address</th><th className="num">Asking price</th></tr></thead>
        <tbody>{rows.map(r => <tr key={r.url}><td>{dateLabel(r.date)}</td><td><a href={r.url}>{r.address}</a></td><td className="num">{usd(r.price)}</td></tr>)}</tbody>
      </table></div> : <p className="card-head">This snapshot contains no listings dated {from === through ? dateLabel(from) : `${dateLabel(from)} through ${dateLabel(through)}`}.</p>}
    </>}
  </section>
}
