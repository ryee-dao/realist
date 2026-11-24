import '../styles/pipeline.css'
import { useEffect, useId, useState } from 'react'
import { Check, City, Index, Run, Status } from '../lib/data'
import { compact, dateLabel, dateTime, monthLabel, num, timeAgo } from '../lib/format'
import LiveTerminal, { Meter } from '../components/LiveFeed'
import { cityStages, hms, secondsToNextRun, sourceState } from '../lib/pipeline'
import ChartCard from '../charts/ChartCard'
import { Segmented } from '../components/bits'
import { useWidth } from '../lib/hooks'
import { niceTicks } from '../charts/scale'

const duration = (s: number) => (s < 90 ? `${Math.round(s)}s` : `${Math.floor(s / 60)}m ${Math.round(s % 60)}s`)
const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1)
const short = (reason: string) => cap(reason.split(' (')[0])
const day = (d: string) => dateLabel(d).replace(/, \d{4}$/, '')
const mark = (c: Check) => (c.status === 'pass' ? '✓' : c.status === 'warn' ? '!' : '✕')
const pct = (n: number, d: number) => (d ? `${Math.round((n / d) * 100)}%` : '–')

function useCountdown(): string {
  const [s, setS] = useState(secondsToNextRun())
  useEffect(() => { const id = setInterval(() => setS(secondsToNextRun()), 1000); return () => clearInterval(id) }, [])
  return hms(s)
}

/** The page-wide city choice, kept in ?city= so a view can be linked. */
function useCityParam(index: Index): [string, (id: string) => void] {
  const ids = index.cities.map((c) => c.id)
  const read = () => {
    const v = new URLSearchParams(window.location.search).get('city')
    return v && (v === 'all' || ids.includes(v)) ? v : ids[0]
  }
  const [city, setCity] = useState(read)
  const set = (id: string) => {
    const u = new URL(window.location.href)
    u.searchParams.set('city', id)
    window.history.replaceState(null, '', u.toString())
    setCity(id)
  }
  return [city, set]
}

export default function Pipeline({ index, status }: { index: Index; status: Status }) {
  const run = status.last
  const [cityId, setCityId] = useCityParam(index)
  if (!run) return <div className="notice">The pipeline has not completed a run yet.</div>

  const city = index.cities.find((c) => c.id === cityId) ?? null
  const checks = city ? run.checks.filter((c) => c.city === city.id) : run.checks
  const warned = checks.filter((c) => c.status === 'warn').length
  const failed = checks.filter((c) => c.status === 'fail').length

  return (
    <div className="pipe">
      <div className="page-head">
        <div>
          <h1>Data pipeline</h1>
          <p className="muted">Public records in, checked numbers out.</p>
        </div>
        <div className="pipe-top">
          <Segmented label="City" value={cityId} onChange={setCityId}
            options={[{ id: 'all', label: 'All cities' }, ...index.cities.map((c) => ({ id: c.id, label: c.name }))]} />
          <div className="pipe-chips">
            <span className="chip" title={dateTime(run.finished)}>Last run {timeAgo(run.finished)} · {duration(run.duration_s)}</span>
            <span className={`chip ${failed ? 'fail' : warned ? 'warn' : 'pass'}`}>
              {failed ? `✕ ${failed} failed` : warned ? `! ${warned} warning${warned > 1 ? 's' : ''}` : `✓ ${checks.length}/${checks.length} checks passed`}
            </span>
          </div>
        </div>
      </div>

      <div className="control">
        {city
          ? <LiveTerminal key={city.id} index={index} city={city.id} lag={[city]} checked={run.finished} />
          : <LiveTerminal key="all" index={index} city={index.cities[0].id} note={`${index.cities[0].name} records · pick a city`} lag={index.cities} checked={run.finished} />}
        <ControlSide index={index} run={run} city={city} />
      </div>

      {city ? <>
        <CityPanel city={city} run={run} />
        <CityGate city={city} run={run} />
        <CitySources city={city} run={run} />
      </> : <>
        <div className="board">
          {index.cities.map((c) => <CityCard key={c.id} city={c} run={run} onOpen={() => setCityId(c.id)} />)}
        </div>
        <AllGate index={index} run={run} />
      </>}

      <SaleMonthsCard index={index} city={city} />

      <section className="card">
          <header className="card-head"><div><h3>Public records</h3><p>The datasets behind every number</p></div></header>
          <div className="verify">
            {(city ? [city] : index.cities).flatMap((c) => [
              ...c.sources.map((s) => <a key={c.id + s.url} href={s.url} target="_blank" rel="noreferrer"><b>{city ? s.name : `${c.name} · ${s.name}`}</b><span>{s.publisher}</span></a>),
              <a key={c.id + 'crime'} href={c.crimeLink} target="_blank" rel="noreferrer"><b>{city ? 'Reported crime incidents' : `${c.name} · reported crime incidents`}</b><span>{c.crimeSource}</span></a>,
            ])}
          </div>
          <p className="footnote pipe-range">{city
            ? `${city.name} sales stored ${dateLabel(city.firstSale)} to ${dateLabel(city.latestSale)}`
            : `Stored sales ${dateLabel(index.cities.map((c) => c.firstSale).sort()[0])} to ${dateLabel(index.cities.map((c) => c.latestSale).sort().reverse()[0])}`}</p>
        </section>
    </div>
  )
}

/* Beside the terminal: the countdown to the next run and a light per source. */
function ControlSide({ index, run, city }: { index: Index; run: Run; city: City | null }) {
  const clock = useCountdown()
  const rows = city ? [city] : index.cities
  return (
    <div className="control-side">
      <section className="card">
        <header className="card-head"><div><h3>Next run in</h3></div><span className="pipe-tag">All cities</span></header>
        <div className="clock" role="timer" aria-label={`Next daily run in ${clock}`}>{clock}</div>
        <div className="lights">
          {rows.map((c) => (
            <div key={c.id}><span>{city ? 'Sources' : c.name}</span>
              <div>{(run.cities[c.id]?.sources ?? []).map((s) => {
                const st = sourceState(s)
                return <i key={s.name} className={`light src-${st}`} title={`${s.name}: ${st}`} />
              })}</div>
            </div>
          ))}
          <div className="lights-key"><span><i className="light src-pulled" />pulled</span><span><i className="light src-unchanged" />unchanged</span><span><i className="light src-failed" />failed</span></div>
        </div>
      </section>
    </div>
  )
}

function Ring({ share, size }: { share: number; size?: 'big' }) {
  const R = 42, C = 2 * Math.PI * R
  return (
    <svg className={`ring ${size ?? ''}`} viewBox="0 0 100 100" role="img" aria-label={`${Math.round(share * 100)}% of records kept`}>
      <circle cx="50" cy="50" r={R} className="ring-track" />
      <circle cx="50" cy="50" r={R} className="ring-fill" strokeDasharray={`${C * share} ${C}`} transform="rotate(-90 50 50)" />
      <text x="50" y="50" textAnchor="middle" className="ring-num">{Math.round(share * 100)}%</text>
      <text x="50" y="66" textAnchor="middle" className="ring-cap">kept</text>
    </svg>
  )
}

function Freshness({ asOf }: { asOf?: string }) {
  return (
    <span className={`pipe-tag ${asOf ? '' : 'fresh'}`} title={asOf ? `Source unchanged in the last run: numbers from its pull of ${dateLabel(asOf)}` : 'Pulled in the last run'}>
      {asOf ? `Unchanged · pull of ${day(asOf)}` : 'Fresh pull'}
    </span>
  )
}

function SegBar({ kept, ranked }: { kept: number; ranked: [string, number][] }) {
  const rejected = ranked.reduce((t, [, n]) => t + n, 0)
  return (
    <div className="seg thin" role="img" aria-label={`${num(kept)} kept, ${num(rejected)} rejected`}>
      <i className="kept" style={{ flexGrow: kept }} title={`Kept: ${num(kept)}`} />
      {ranked.map(([reason, n], i) => <i key={reason} className={`rej r${Math.min(i, 3)}`} style={{ flexGrow: n }} title={`${short(reason)}: ${num(n)}`} />)}
    </div>
  )
}

function Meters({ city }: { city: City }) {
  return (
    <div className="meters">
      <Meter label="Area" v={city.coverage.area} /><Meter label="Map point" v={city.coverage.coordinates} /><Meter label="Floor area" v={city.coverage.sqft} />
    </div>
  )
}

/* City view: the one city in detail, every rejection reason with its count. */
function CityPanel({ city, run }: { city: City; run: Run }) {
  const s = cityStages(run, city.id)
  const share = s.read ? s.kept / s.read : 0
  const ok = s.passed === s.checks.length
  return (
    <section className="card city-panel">
      <header className="card-head"><div><h3>{city.name} · kept and thrown out</h3><p>{dateTime(run.finished)} · {ok ? `${s.passed}/${s.checks.length} checks passed` : `${s.passed}/${s.checks.length} checks passed, needs a look`}</p></div><Freshness asOf={s.asOf} /></header>
      <div className="city-panel-grid">
        <div className="city-panel-left">
          <div className="ring-row">
            <Ring share={share} size="big" />
            <dl className="stats">
              <div><dt>Read</dt><dd>{num(s.read)}</dd></div>
              <div><dt>Kept</dt><dd>{num(s.kept)}</dd></div>
              <div><dt>Rejected</dt><dd>{num(s.read - s.kept)}</dd></div>
              <div><dt>Stored</dt><dd>{num(city.rows)}</dd></div>
              <div><dt>Latest sale</dt><dd>{dateLabel(city.latestSale)}</dd></div>
              <div><dt>Checks</dt><dd className="pipe-lights">{s.checks.map((k) => <i key={k.name} className={`light ${k.status}`} title={`${k.name}: ${k.detail}`} />)}</dd></div>
            </dl>
          </div>
          <div className="tile-label">Coverage · last 12 months</div>
          <Meters city={city} />
        </div>
        <div className="city-panel-right">
          {s.read > 0 ? <>
            <SegBar kept={s.kept} ranked={s.ranked} />
            <ul className="reasons">
              <li className="kept"><i className="key-box kept" /><span>Kept</span><b>{num(s.kept)}</b><em>{pct(s.kept, s.read)}</em></li>
              {s.ranked.map(([reason, n], i) => (
                <li key={reason} title={reason}><i className={`key-box rej r${Math.min(i, 3)}`} /><span>{short(reason)}</span><b>{num(n)}</b><em>{pct(n, s.read)}</em></li>
              ))}
            </ul>
          </> : <div className="muted">No sale records read.</div>}
        </div>
      </div>
    </section>
  )
}

/* All-cities view: three compact cards. */
function CityCard({ city, run, onOpen }: { city: City; run: Run; onOpen: () => void }) {
  const f = cityStages(run, city.id)
  const share = f.read ? f.kept / f.read : 0
  return (
    <section className="card board-col">
      <header className="card-head">
        <div><h3><button className="pipe-link" onClick={onOpen}>{city.name} ›</button></h3></div>
        <Freshness asOf={f.asOf} />
      </header>
      <div className="ring-row">
        <Ring share={share} />
        <dl className="kv">
          <dt>Read</dt><dd>{num(f.read)}</dd>
          <dt>Kept</dt><dd>{num(f.kept)}</dd>
          <dt>Latest sale</dt><dd>{dateLabel(city.latestSale)}</dd>
          <dt>Checks</dt><dd className="pipe-lights">{f.checks.map((k) => <i key={k.name} className={`light ${k.status}`} title={`${k.name}: ${k.detail}`} />)}</dd>
        </dl>
      </div>
      {f.read > 0 && <>
        <SegBar kept={f.kept} ranked={f.ranked} />
        <div className="legend pipe-legend">
          <span title={`Kept: ${num(f.kept)}`}><i className="key-box kept" />Kept</span>
          {f.ranked.slice(0, 3).map(([reason, n], i) => <span key={reason} title={`${short(reason)}: ${num(n)}`}><i className={`key-box rej r${i}`} />{short(reason)}</span>)}
        </div>
      </>}
      <Meters city={city} />
    </section>
  )
}

/* City view: that city's checks with their evidence. */
function CityGate({ city, run }: { city: City; run: Run }) {
  const checks = run.checks.filter((c) => c.city === city.id)
  return (
    <section className="card">
      <header className="card-head"><div><h3>Quality gate</h3><p>Any failure keeps yesterday's data live.</p></div></header>
      <ul className="gate-list">
        {checks.map((c) => (
          <li key={c.name}><span className={`chip ${c.status}`}>{mark(c)}</span><b>{c.name}</b><span className="muted">{c.detail}</span></li>
        ))}
      </ul>
    </section>
  )
}

function SourcesTable({ rows }: { rows: { city?: string; s: Run['cities'][string]['sources'][number] }[] }) {
  return (
    <div className="table-scroll">
      <table className="data pipe-src">
        <thead><tr>{rows[0]?.city !== undefined && <th>City</th>}<th>Source</th><th className="num">Read</th><th className="num">Kept</th><th className="num">New</th><th className="num">Req.</th><th>Result</th></tr></thead>
        <tbody>
          {rows.map(({ city, s }) => {
            const st = sourceState(s)
            const last = st === 'unchanged' && s.last ? s.last : null
            const was = last ? `From the pull of ${dateLabel(last.date)}; not re-read this run` : undefined
            return (
              <tr key={(city ?? '') + s.name}>
                {city !== undefined && <td>{city}</td>}
                <td><b>{s.name}</b><small><code>{s.dataset}</code>{s.context && ' · context'}</small></td>
                <td className={`num ${last ? 'muted' : ''}`} title={was}>{num(last ? last.fetched : s.fetched)}</td><td className={`num ${last ? 'muted' : ''}`} title={was}>{num(last ? last.kept : s.kept)}</td><td className="num">{s.context ? '–' : num(s.new)}</td><td className="num">{num(s.requests)}</td>
                <td><span className={`chip ${st === 'pulled' ? 'pass' : st === 'failed' ? 'warn' : ''}`} title={s.skipped ?? undefined}>{st === 'pulled' ? '✓ Pulled' : st === 'failed' ? '! Failed' : last ? `Unchanged · pull of ${day(last.date)}` : 'Unchanged'}</span></td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

/* City view: that city's sources, sales and context, from the last run. */
function CitySources({ city, run }: { city: City; run: Run }) {
  const sources = run.cities[city.id]?.sources ?? []
  return (
    <section className="card">
      <header className="card-head"><div><h3>Sources</h3><p>Last run · grey numbers are from an unchanged source's last pull</p></div></header>
      <SourcesTable rows={sources.map((s) => ({ s }))} />
    </section>
  )
}

/* All-cities view: badges per city, evidence and sources behind toggles. */
function AllGate({ index, run }: { index: Index; run: Run }) {
  const [evidence, setEvidence] = useState(false)
  const [sources, setSources] = useState(false)
  return (
    <section className="card">
      <header className="card-head">
        <div><h3>Quality gate</h3><p>Any failure keeps yesterday's data live.</p></div>
        <div className="pipe-buttons">
          <button className="ghost small" onClick={() => setEvidence(!evidence)} aria-pressed={evidence}>{evidence ? 'Hide evidence' : 'Show evidence'}</button>
          <button className="ghost small" onClick={() => setSources(!sources)} aria-pressed={sources} aria-expanded={sources}>{sources ? 'Hide sources' : 'Show sources'}</button>
        </div>
      </header>
      <div className="gate">
        {index.cities.map((city) => (
          <div key={city.id} className="gate-row">
            <b>{city.name}</b>
            <div className="gate-checks">
              {run.checks.filter((c) => c.city === city.id).map((c) => (
                <span key={c.name} className={`chip ${c.status}`} title={c.detail}>{mark(c)} {c.name}{evidence && <em> · {c.detail}</em>}</span>
              ))}
            </div>
          </div>
        ))}
      </div>
      {sources && <div className="pipe-sources"><SourcesTable rows={index.cities.flatMap((c) => (run.cities[c.id]?.sources ?? []).map((s) => ({ city: c.name, s })))} /></div>}
    </section>
  )
}

/* Sales held in the store by the month the sale happened. Months after `through` are still being published by the city. */
function SaleMonthsCard({ index, city }: { index: Index; city: City | null }) {
  const shown = city ? [city] : index.cities
  const top = Math.max(1, ...shown.flatMap((c) => c.byMonth.map((b) => b.count)))
  const months = shown[0]?.byMonth.map((b) => b.month) ?? []
  const filling = (c: City, m: string) => m > c.through
  const table = city
    ? { head: ['Sale month', 'Sales', 'Status'], rows: [...city.byMonth].reverse().map((b) => [monthLabel(b.month), num(b.count), filling(city, b.month) ? 'City still publishing' : 'Complete']) }
    : { head: ['Sale month', ...index.cities.map((c) => c.name)], rows: [...new Set(index.cities.flatMap((c) => c.byMonth.map((b) => b.month)))].sort().reverse()
        .map((m) => [monthLabel(m), ...index.cities.map((c) => { const b = c.byMonth.find((x) => x.month === m); return b ? `${num(b.count)}${filling(c, m) ? ' *' : ''}` : '–' })]) }
  const latest = city ? city.latestSale : index.cities.map((c) => c.latestSale).sort().reverse()[0]
  return (
    <ChartCard title="Sales by sale date" subtitle={`${city ? city.name : 'All cities'} · by month · latest sale ${dateLabel(latest)}`} table={table}>
      <div className="legend sm-legend">
        <span><i className="key-box sm-key" />Complete</span>
        <span><i className="key-box sm-key filling" />City still publishing these months</span>
        {!city && <span className="muted">Same scale</span>}
      </div>
      <div className={`sm-grid ${city ? 'one' : ''}`}>
        {shown.map((c) => <SaleMonths key={c.id} city={c} top={top} title={city ? undefined : c.name} height={city ? 220 : 170} />)}
      </div>
      {months.length === 0 && <div className="muted">No sales stored.</div>}
      <p className="footnote sm-note">Deeds reach the public record weeks to months after a sale, so the newest months are incomplete.{!city && ' * marks those months.'}</p>
    </ChartCard>
  )
}

const SM = { top: 10, right: 6, bottom: 22, left: 44 }
const shortMonth = (m: string) => `${monthLabel(m).slice(0, 3)} ${m.slice(2, 4)}`

function SaleMonths({ city, top: max, title, height }: { city: City; top: number; title?: string; height: number }) {
  const [ref, width] = useWidth<HTMLDivElement>()
  const [hover, setHover] = useState<number | null>(null)
  const hatch = `hatch-${useId().replace(/:/g, '')}`
  const data = city.byMonth
  const n = data.length
  const w = Math.max(10, width - SM.left - SM.right)
  const h = height - SM.top - SM.bottom
  const yt = niceTicks(0, max, 4)
  const top = yt[yt.length - 1]
  const band = w / Math.max(1, n)
  const bar = Math.max(1, Math.min(22, band - 2))
  const y = (v: number) => SM.top + h - (v / top) * h
  const cx = (i: number) => SM.left + band * (i + 0.5)
  const split = data.findIndex((b) => b.month > city.through)   // first month still filling in
  const onMove = (e: React.PointerEvent) => {
    const box = (e.currentTarget as SVGElement).getBoundingClientRect()
    const i = Math.floor((e.clientX - box.left - SM.left) / band)
    setHover(i >= 0 && i < n ? i : null)
  }
  const tipLeft = hover == null ? 0 : cx(hover)
  const flip = tipLeft > width * 0.55
  const splitTick = split > 0 && split * band >= 52 && (n - 1 - split) * band >= 52   // label the split only where it cannot collide
  const ticks = n ? [0, ...(splitTick ? [split] : []), n - 1].filter((v, i, a) => a.indexOf(v) === i) : []
  return (
    <div className="sm">
      {title && <div className="sm-title">{title}</div>}
      <div className="chart sm-chart" ref={ref} style={{ minHeight: height }}>
        {width > 0 && (
          <svg width={width} height={height} role="img" aria-label={`${city.name}: sales per sale month; complete through ${monthLabel(city.through)}`}
            onPointerMove={onMove} onPointerLeave={() => setHover(null)}>
            <defs>
              <pattern id={hatch} width="5" height="5" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
                <rect width="5" height="5" className="sm-hatch-bg" /><line x1="0" y1="0" x2="0" y2="5" className="sm-hatch-line" />
              </pattern>
            </defs>
            {yt.map((t) => (
              <g key={t}>
                <line className={t === 0 ? 'baseline' : 'grid'} x1={SM.left} x2={SM.left + w} y1={y(t)} y2={y(t)} />
                <text className="axis" x={SM.left - 8} y={y(t)} dy="0.32em" textAnchor="end">{compact(t)}</text>
              </g>
            ))}
            {split > 0 && <line className="sm-split" x1={SM.left + band * split} x2={SM.left + band * split} y1={SM.top} y2={SM.top + h} />}
            {data.map((b, i) => {
              if (b.count <= 0) return null
              const bh = Math.max(1, SM.top + h - y(b.count))
              const r = Math.min(4, bar / 2, bh)
              const x0 = cx(i) - bar / 2
              const yTop = SM.top + h - bh
              const fill = b.month > city.through
              return (
                <path key={b.month} className={fill ? 'sm-bar filling' : 'sm-bar'} style={fill ? { fill: `url(#${hatch})` } : undefined}
                  opacity={hover == null || hover === i ? 1 : 0.55}
                  d={`M${x0},${SM.top + h}V${yTop + r}Q${x0},${yTop} ${x0 + r},${yTop}H${x0 + bar - r}Q${x0 + bar},${yTop} ${x0 + bar},${yTop + r}V${SM.top + h}Z`} />
              )
            })}
            {ticks.map((i, k) => (
              <text key={i} className="axis" x={cx(i)} y={height - 6} textAnchor={k === 0 ? 'start' : k === ticks.length - 1 ? 'end' : 'middle'}
                dx={k === 0 ? -bar / 2 : k === ticks.length - 1 ? bar / 2 : 0}>{shortMonth(data[i].month)}</text>
            ))}
          </svg>
        )}
        {hover != null && data[hover] && (
          <div className="tooltip" style={flip ? { right: width - tipLeft + 12, top: SM.top } : { left: tipLeft + 12, top: SM.top }}>
            <div className="tooltip-title">{monthLabel(data[hover].month)}</div>
            <div className="tooltip-row"><span>Sales held</span><b>{num(data[hover].count)}</b></div>
            <div className="tooltip-row"><span>{data[hover].month > city.through ? 'City still publishing' : 'Complete'}</span></div>
          </div>
        )}
      </div>
    </div>
  )
}
