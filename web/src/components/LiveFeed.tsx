// The intake terminal on the pipeline page. It replays the newest records the engine read on its last real pull of
// a city (data/<city>/intake.json, written by the engine), with the verdict each got. The browser never calls a
// city's API: it re-reads our own file every few minutes and animates new rows in when the engine has read again.
import { useEffect, useRef, useState } from 'react'
import { City, Index, Intake, IntakeRecord } from '../lib/data'
import { dateLabel, usd } from '../lib/format'
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

async function fetchIntake(city: string): Promise<Intake> {
  const res = await fetch(`data/${city}/intake.json?t=${Date.now()}`, { cache: 'no-store' })
  if (!res.ok) throw new Error(res.status === 404 ? 'no sample yet' : `could not load (${res.status})`)
  return res.json()
}

function useNextRun(): string {
  const [s, setS] = useState(secondsToNextRun())
  useEffect(() => { const id = setInterval(() => setS(secondsToNextRun()), 1000); return () => clearInterval(id) }, [])
  return hms(s)
}

interface Loaded { data: Intake; fresh: Set<string>; first: boolean }

export default function LiveTerminal({ index, city, note, lag, checked }: { index: Index; city: string; note?: string; lag: City[]; checked: string }) {
  const [loaded, setLoaded] = useState<Loaded | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [shown, setShown] = useState(0)
  const seen = useRef<{ readAt: string | null; keys: Set<string> } | null>(null)
  const next = useNextRun()

  useEffect(() => {
    let live = true
    seen.current = null
    setLoaded(null); setError(null)
    const load = () => {
      if (document.hidden) return
      fetchIntake(city).then(
        (d) => {
          if (!live) return
          const prev = seen.current
          if (prev && prev.readAt === d.readAt) return          // nothing new since the last look
          const keys = d.records.map(rowKey)
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
  }, [city])

  useEffect(() => {   // on first load, print the lines one by one, as the engine judged them
    if (!loaded || shown >= loaded.data.records.length) return
    const id = window.setTimeout(() => setShown((n) => n + 1), 60)
    return () => clearTimeout(id)
  }, [loaded, shown])

  const data = loaded?.data
  const rows = data?.records.slice(0, shown) ?? []
  const done = !!data && shown >= data.records.length
  const keptN = data ? data.records.filter((r) => r.verdict === 'keep').length : 0
  const name = index.cities.find((c) => c.id === city)?.name ?? city
  const stored = data?.kind === 'stored'

  return (
    <section className="card term">
      <header className="card-head">
        <div>
          <h3><i className={`pulse ${data && !error ? 'on' : ''}`} />Intake</h3>
          <p>{error ? `No intake sample for ${name}: ${error}` : data ? 'Newest sales we hold, and what we did with each' : `Loading ${name}…`}</p>
        </div>
        <span className="pipe-when">{note ?? name}</span>
      </header>
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
    </section>
  )
}
