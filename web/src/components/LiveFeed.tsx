// The live intake terminal on the pipeline page. It calls each city's public API straight from the visitor's
// browser when the page opens and every 60 seconds, and shows the engine's keep-or-reject verdict per record.
import { useEffect, useRef, useState } from 'react'
import { Index } from '../lib/data'
import { dateLabel, usd } from '../lib/format'
import { fetchLive, LiveResult, LiveRow } from '../lib/live'

const REFRESH = 60

export function Meter({ label, v }: { label: string; v: number }) {
  return (
    <div className="meter" title={`${v}% of the last 12 months of sales have this`}>
      <span>{label}</span><div className="bar-track"><div className="bar-fill" style={{ width: `${v}%` }} /></div><span className="num">{v.toFixed(0)}%</span>
    </div>
  )
}

const reducedMotion = () => typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches
const rowKey = (r: LiveRow) => `${r.date}|${r.address}|${r.price}|${r.kind}`
const clock = (t: number) => new Date(t).toLocaleTimeString('en-GB', { hour12: false })

interface Pull { data: LiveResult; fresh: Set<string>; first: boolean }

export default function LiveTerminal({ index, city, note }: { index: Index; city: string; note?: string }) {
  const [pull, setPull] = useState<Pull | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(true)
  const [shown, setShown] = useState(0)
  const [left, setLeft] = useState(REFRESH)
  const seen = useRef<Set<string> | null>(null)

  useEffect(() => {
    let live = true
    seen.current = null
    const run = () => {
      setBusy(true)
      fetchLive(city).then(
        (d) => {
          if (!live) return
          const keys = d.rows.map(rowKey)
          const first = seen.current == null
          const fresh = new Set(first ? keys : keys.filter((k) => !seen.current!.has(k)))
          seen.current = new Set(keys)
          setPull({ data: d, fresh, first })
          setShown(first && !reducedMotion() ? 0 : d.rows.length)
          setError(null); setBusy(false); setLeft(REFRESH)
        },
        (e) => { if (live) { setError(String(e?.message || e)); setBusy(false); setLeft(REFRESH) } },
      )
    }
    setPull(null); setError(null)
    run()
    const id = window.setInterval(() => setLeft((s) => { if (s <= 1) { run(); return REFRESH } return s - 1 }), 1000)
    return () => { live = false; clearInterval(id) }
  }, [city])

  useEffect(() => {   // on the first pull for a city, print lines one by one as they are checked
    if (!pull || shown >= pull.data.rows.length) return
    const id = window.setTimeout(() => setShown((n) => n + 1), 60)
    return () => clearTimeout(id)
  }, [pull, shown])

  const data = pull?.data
  const rows = data?.rows.slice(0, shown) ?? []
  const done = !!data && shown >= data.rows.length
  const keptN = data ? data.rows.filter((r) => !r.verdict).length : 0
  const name = index.cities.find((c) => c.id === city)?.name ?? city
  const newCount = pull && !pull.first ? pull.fresh.size : null

  return (
    <section className="card term">
      <header className="card-head">
        <div>
          <h3><i className={`pulse ${data && !error ? 'on' : ''}`} />Live intake</h3>
          <p>{error ? `Could not reach the source: ${error}` : data ? `Newest at source ${dateLabel(data.rows[0]?.date ?? '')} · records publish weeks late` : `Asking ${name} for its newest records…`}</p>
        </div>
        <span className="pipe-when">{note ?? name}</span>
      </header>
      <div className="term-box">
        <div className="term-body" aria-live="off">
          <div className="term-line cmd"><span className="term-prompt">$</span> GET {data?.host ?? '…'} <em>newest {data?.rows.length ?? 40}</em></div>
          {data && <div className="term-line cmd"><span className="term-ok">200</span> {data.ms} ms · {clock(data.fetchedAt)}{newCount != null && <em> · {newCount ? `${newCount} new since last pull` : 'no new records since last pull'}</em>}</div>}
          {error && <div className="term-line cmd"><span className="term-err">ERR</span> {error}</div>}
          {rows.map((r, i) => {
            const k = rowKey(r)
            return (
              <div key={`${k}#${i}`} className={`term-line rec ${r.verdict ? 'rej' : 'ok'} ${pull!.fresh.has(k) ? 'arrive' : ''}`}>
                <span>{r.date}</span><b>{r.verdict ? 'REJECT' : 'KEEP'}</b><span className="num">{r.price == null ? '–' : usd(r.price)}</span>
                <span className="term-addr">{r.address || '–'}</span><em>{r.verdict ?? r.kind}</em>
              </div>
            )
          })}
          {done && <div className="term-line cmd"><em>{data!.rows.length} read · {keptN} kept · {data!.rows.length - keptN} rejected</em></div>}
          {!busy && <div className="term-line cursor">▍</div>}
        </div>
        <div className="term-foot">
          <span>{busy ? 'pulling…' : `next pull in ${left}s`}</span>
          <span>in your browser · engine rules</span>
        </div>
      </div>
    </section>
  )
}
