// The intake terminal on the pipeline page. It replays the newest records the engine read on its last real pull of
// a city (data/<city>/intake.json, written by the engine), with the verdict each got. The browser never calls a
// city's API: it re-reads our own file every few minutes and animates new rows in when the engine has read again.
// A second feed, picked with a toggle, shows the newest homes for sale the listing harvest took in
// (data/<city>/listings-intake.json); it stays hidden when that file is missing.
import { useEffect, useRef, useState } from 'react'
import { City, Index, Intake, IntakeRecord, ListingIntake, ListingIntakeRecord } from '../lib/data'
import { dateLabel, usd, usdCompact } from '../lib/format'
import { Segmented } from './bits'
import { hms, secondsToNextRun } from '../lib/pipeline'

const RECHECK_MS = 5 * 60 * 1000

export function Meter({ label, v }: { label: string; v: number }) {
  return (
    <div className="meter" title={`${v}% of the last 12 months of sales have this`}>
      <span>{label}</span><div className="bar-track"><div className="bar-fill" style={{ width: `${v}%` }} /></div><span className="num">{v.toFixed(0)}%</span>
    </div>
  )
}

const reducedMotion = () => typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches
const rowKey = (r: IntakeRecord) => `${r.date}|${r.address}|${r.price}|${r.kind}|${r.verdict}`
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
const utc = (iso: string) => {
  const d = new Date(iso)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${MONTHS[d.getUTCMonth()]} ${d.getUTCDate()}, ${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())} UTC`
}

const reasonLabel = (r: string | null) => { const t = (r ?? '').split(' (')[0]; return t.charAt(0).toUpperCase() + t.slice(1) }
const cap = (t: string) => t.charAt(0).toUpperCase() + t.slice(1)
const listingKey = (r: ListingIntakeRecord) => `${r.firstSeen ?? r.offAt}|${r.address}|${r.price}|${r.tag}`

async function fetchJson<T>(path: string): Promise<T> {
  const res = await fetch(`data/${path}?t=${Date.now()}`, { cache: 'no-store' })
  if (!res.ok) throw new Error(res.status === 404 ? 'no sample yet' : `could not load (${res.status})`)
  return res.json()
}

function useNextRun(): string {
  const [s, setS] = useState(secondsToNextRun())
  useEffect(() => { const id = setInterval(() => setS(secondsToNextRun()), 1000); return () => clearInterval(id) }, [])
  return hms(s)
}

interface Feed<R> { readAt: string | null; records: R[] }
interface Loaded<D> { data: D; fresh: Set<string>; first: boolean }

/** Re-read one of our own intake files every few minutes; new rows animate in. `play` starts the line-by-line
 *  print on first show (the listings feed waits until it is picked). */
function useReplay<R, D extends Feed<R>>(path: string, key: (r: R) => string, play: boolean) {
  const [loaded, setLoaded] = useState<Loaded<D> | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [shown, setShown] = useState(0)
  const seen = useRef<{ readAt: string | null; keys: Set<string> } | null>(null)

  useEffect(() => {
    let live = true
    seen.current = null
    setLoaded(null); setError(null)
    const load = () => {
      if (document.hidden) return
      fetchJson<D>(path).then(
        (d) => {
          if (!live || !Array.isArray(d?.records)) return
          const prev = seen.current
          if (prev && prev.readAt === d.readAt) return          // nothing new since the last look
          const keys = d.records.map(key)
          const fresh = new Set(prev ? keys.filter((k) => !prev.keys.has(k)) : keys)
          seen.current = { readAt: d.readAt, keys: new Set(keys) }
          setLoaded({ data: d, fresh, first: !prev })
          setShown(!prev && !reducedMotion() ? 0 : d.records.length)
          setError(null)
        },
        (e) => { if (live && !seen.current) setError(String(e?.message || e)) },
      )
    }
    load()
    const id = window.setInterval(load, RECHECK_MS)
    const onVisible = () => { if (!document.hidden) load() }
    document.addEventListener('visibilitychange', onVisible)
    return () => { live = false; clearInterval(id); document.removeEventListener('visibilitychange', onVisible) }
  }, [path])   // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {   // on first show, print the lines one by one, as the engine judged them
    if (!play || !loaded || shown >= loaded.data.records.length) return
    const id = window.setTimeout(() => setShown((n) => n + 1), 60)
    return () => clearTimeout(id)
  }, [loaded, shown, play])

  return { loaded, error, shown }
}

type Stream = 'sales' | 'listings'

export default function LiveTerminal({ index, city, note, lag, checked }: { index: Index; city: string; note?: string; lag: City[]; checked: string }) {
  const [stream, setStream] = useState<Stream>('sales')
  const sales = useReplay<IntakeRecord, Intake>(`${city}/intake.json`, rowKey, stream === 'sales')
  const listings = useReplay<ListingIntakeRecord, ListingIntake>(`${city}/listings-intake.json`, listingKey, stream === 'listings')
  const next = useNextRun()
  const hasListings = !!listings.loaded && listings.loaded.data.records.length > 0
  const showListings = stream === 'listings' && hasListings
  const name = index.cities.find((c) => c.id === city)?.name ?? city
  const { loaded, error, shown } = sales
  const data = loaded?.data
  const rows = data?.records.slice(0, shown) ?? []
  const done = !!data && shown >= data.records.length
  const keptN = data ? data.records.filter((r) => r.verdict === 'keep').length : 0
  const stored = data?.kind === 'stored'

  return (
    <section className="card term">
      <header className="card-head">
        <div>
          <h3><i className={`pulse ${data && !error ? 'on' : ''}`} />Intake</h3>
          <p>{error && !hasListings ? `No intake sample for ${name}: ${error}` : data || hasListings
            ? (hasListings ? 'Newest sales and listings we read, and what we did with each' : 'Newest sales we hold, and what we did with each')
            : `Loading ${name}…`}</p>
        </div>
        <span className="pipe-when">{note ?? name}</span>
      </header>
      {hasListings && <Segmented label="Show" value={stream} onChange={setStream}
        options={[{ id: 'sales', label: 'Sales (public records)' }, { id: 'listings', label: 'Listings (for sale now)' }]} />}
      {showListings
        ? <ListingsFeed data={listings.loaded!.data} fresh={listings.loaded!.fresh} shown={listings.shown} city={city} name={name} next={next} />
        : <>
      <ul className="lag">
        {lag.map((c) => <li key={c.id}>{lag.length > 1 && <b>{c.name}</b>}<span>Newest sale the city has published: {dateLabel(c.latestSale)}</span><em>checked {dateLabel(checked.slice(0, 10)).replace(/, \d{4}$/, '')}</em></li>)}
      </ul>
      <div className="term-box">
        <div className="term-body" aria-live="off">
          <div className="term-line cmd"><span className="term-prompt">$</span> realist intake --city {city}</div>
          {data && (stored
            ? <div className="term-line cmd"><span className="term-ok">stored</span> <em>latest stored sales · {data.records.length} newest · no intake sample yet</em></div>
            : <div className="term-line cmd"><span className="term-ok">read</span> {data.readAt ? utc(data.readAt) : '–'} <em>· {data.records.length} newest records · from our last run</em></div>)}
          {error && <div className="term-line cmd"><span className="term-err">ERR</span> {error}</div>}
          {rows.map((r, i) => {
            const k = rowKey(r)
            const rej = r.verdict === 'reject'
            return (
              <div key={`${k}#${i}`} className={`term-line rec ${rej ? 'rej' : 'ok'} ${loaded!.fresh.has(k) ? 'arrive' : ''}`} title={r.source === 'stored' ? undefined : r.source}>
                <span>{r.date}</span><b>{rej ? 'REJECT' : 'KEEP'}</b><span className="num">{r.price == null ? '–' : usd(r.price)}</span>
                <span className="term-addr">{r.address || '–'}</span><em title={r.reason ?? undefined}>{rej ? reasonLabel(r.reason) : r.kind}</em>
              </div>
            )
          })}
          {done && <div className="term-line cmd"><em>{data!.records.length} shown · {keptN} kept · {data!.records.length - keptN} rejected</em></div>}
          {done && <div className="term-line cursor">▍</div>}
        </div>
        <div className="term-foot">
          <span>next run in {next}</span>
          <span>{stored ? 'from the store' : "from the engine's last run"}</span>
        </div>
      </div>
      </>}
    </section>
  )
}

function ListingsFeed({ data, fresh, shown, city, name, next }: { data: ListingIntake; fresh: Set<string>; shown: number; city: string; name: string; next: string }) {
  const rows = data.records.slice(0, shown)
  const done = shown >= data.records.length
  const cityTail = new RegExp(`\\s+${name.replace(/[^\w ]/g, '.')}$`, 'i')   // "7154 Kindred St Philadelphia" -> the street
  const c = data.counts ?? {}
  const off = data.offMarket ?? { count: 0, shown: 0 }
  const hidden = off.count - off.shown
  const tally = [
    c.new && `${c.new} new`, c.merged && `${c.merged} merged`, c.price && `${c.price} price changed`,
    c.unseen && `${c.unseen} not seen lately`, c.off && `${c.off} taken off`,
  ].filter(Boolean).join(' · ')
  const skipped = data.skipped ?? {}
  const skipText = [skipped.date && `${skipped.date} listed over a week ago`, skipped.price && `${skipped.price} with no usable price`].filter(Boolean).join(', ')
  return <>
    <div className="term-box">
      <div className="term-body" aria-live="off">
        <div className="term-line cmd"><span className="term-prompt">$</span> realist listings --city {city}</div>
        <div className="term-line cmd"><span className="term-ok">read</span> {data.readAt ? utc(data.readAt) : '–'} <em>· {data.records.length} newest homes · prices are asking prices</em></div>
        {rows.map((r, i) => {
          const k = listingKey(r)
          const drop = r.verdict === 'drop'
          const warn = r.tag === 'unseen'
          const what = [cap(r.reason), r.type].filter(Boolean).join(' · ')
          const street = (r.address ?? '').split(',')[0].replace(cityTail, '').trim()
          return (
            <div key={`${k}#${i}`} className={`term-line rec lst ${drop ? 'rej' : 'ok'} ${warn ? 'warn' : ''} ${fresh.has(k) ? 'arrive' : ''}`}>
              <span>{r.date ? <><span className="yr">{r.date.slice(0, 5)}</span>{r.date.slice(5)}</> : '–'}</span><b>{drop ? 'DROP' : 'KEEP'}</b>
              <span className="num" title="Asking price">{r.price == null ? '–' : <>{usdCompact(r.price)} <small>ask</small></>}</span>
              <span className="term-addr" title={r.address ?? undefined}>{street || '–'}</span>
              <em className={warn || drop ? 'flag' : 'plain'} title={what}>{warn && <i className="warn-mark" aria-hidden="true">!</i>}{cap(r.reason)}{r.type && <span className="term-type"> · {r.type}</span>}</em>
            </div>
          )
        })}
        {done && <div className="term-line cmd"><em>{data.records.length} shown{tally ? ` · ${tally}` : ''}</em></div>}
        {done && hidden > 0 && <div className="term-line cmd"><em>{hidden} taken off: no longer for sale</em></div>}
        {done && skipText && <div className="term-line cmd"><em>skipped while reading: {skipText}</em></div>}
        {done && <div className="term-line cursor">▍</div>}
      </div>
      <div className="term-foot">
        <span>next run in {next}</span>
        <span>{data.homes} homes for sale on file</span>
      </div>
    </div>
  </>
}
