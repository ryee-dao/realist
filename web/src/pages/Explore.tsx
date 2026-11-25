import '../styles/explore.css'
import { useEffect, useMemo, useRef, useState } from 'react'
import { City, useData } from '../lib/data'
import type { Loaded, Progress, QueryManifest, Result, Scope } from '../lib/duck'
import { dateLabel, num, usd, usdCompact } from '../lib/format'
import { AREA_TYPES, SALES_TYPES } from '../lib/querySchema'

type Duck = typeof import('../lib/duck')
const PAGE = 25

// ---------- shareable state: everything after '?' in the hash ----------
function readHash(): URLSearchParams {
  const h = window.location.hash
  const i = h.indexOf('?')
  return new URLSearchParams(i >= 0 ? h.slice(i + 1) : '')
}
function writeHash(params: URLSearchParams) {
  const qs = params.toString()
  const route = window.location.hash.split('?')[0] || '#/'
  window.history.replaceState(null, '', `${window.location.pathname}${window.location.search}${route}${qs ? `?${qs}` : ''}`)
}

// ---------- filters ----------
interface Filters {
  places: string[]            // area names, or 5-digit ZIPs
  lo: number | null; hi: number | null
  beds: string                // '', '1'..'4', '5' (= 5+)
  types: string[]
  within: number              // months back from the latest sale; 0 = everything loaded
  s1: number | null; s2: number | null
}
type Group = 'none' | 'area' | 'year' | 'type'
type Metric = 'sales' | 'price' | 'ppsf'
type Order = 'new' | 'old' | 'priceDown' | 'priceUp' | 'ppsfUp' | 'big'
interface View { group: Group; metric: Metric; asc: boolean; order: Order }

const NO_FILTERS: Filters = { places: [], lo: null, hi: null, beds: '', types: [], within: 12, s1: null, s2: null }
const DEFAULT_VIEW: View = { group: 'none', metric: 'sales', asc: false, order: 'new' }

const PRICES = [50_000, 100_000, 150_000, 200_000, 250_000, 300_000, 400_000, 500_000, 750_000, 1_000_000, 1_500_000, 2_000_000, 3_000_000]
const SIZES = [500, 750, 1000, 1250, 1500, 2000, 2500, 3000, 4000]
const WITHIN: { m: number; label: string }[] = [
  { m: 6, label: '6 mo' }, { m: 12, label: '1 yr' }, { m: 24, label: '2 yr' }, { m: 36, label: '3 yr' }, { m: 60, label: '5 yr' }, { m: 0, label: 'All' },
]
const ORDERS: { id: Order; label: string }[] = [
  { id: 'new', label: 'Newest' }, { id: 'old', label: 'Oldest' }, { id: 'priceDown', label: 'Price: high to low' },
  { id: 'priceUp', label: 'Price: low to high' }, { id: 'ppsfUp', label: '$/sq ft: low to high' }, { id: 'big', label: 'Biggest' },
]
const MIN_RANKED = 5   // groups with fewer sales are listed but not ranked by price

const lit = (s: string) => `'${s.replace(/'/g, "''")}'`
const isZip = (s: string) => /^\d{5}$/.test(s)
const PPSF_OK = 'sqft > 0 AND price / sqft BETWEEN 10 AND 5000'

function whereSql(f: Filters, latest: string): string {
  const w: string[] = []
  const zips = f.places.filter(isZip), areas = f.places.filter((p) => !isZip(p))
  const place = [areas.length ? `area IN (${areas.map(lit).join(', ')})` : '', zips.length ? `zip IN (${zips.map(lit).join(', ')})` : ''].filter(Boolean)
  if (place.length) w.push(place.length > 1 ? `(${place.join(' OR ')})` : place[0])
  if (f.within > 0) w.push(`date > DATE ${lit(latest)} - INTERVAL ${f.within} MONTH`)
  if (f.lo != null) w.push(`price >= ${Math.round(f.lo)}`)
  if (f.hi != null) w.push(`price <= ${Math.round(f.hi)}`)
  if (f.beds) w.push(f.beds === '5' ? 'beds >= 5' : `beds = ${+f.beds}`)
  if (f.types.length) w.push(`type IN (${f.types.map(lit).join(', ')})`)
  if (f.s1 != null) w.push(`sqft >= ${Math.round(f.s1)}`)
  if (f.s2 != null) w.push(`sqft <= ${Math.round(f.s2)}`)
  return w.length ? `WHERE ${w.join('\n  AND ')}` : ''
}
const and = (where: string, cond: string) => (where ? `${where}\n  AND ${cond}` : `WHERE ${cond}`)

const summarySql = (where: string) => `SELECT count(*) AS n, median(price) AS med,
  quantile_cont(price, 0.25) AS p25, quantile_cont(price, 0.75) AS p75,
  median(price / sqft) FILTER (WHERE ${PPSF_OK}) AS ppsf, count(*) FILTER (WHERE ${PPSF_OK}) AS sized,
  min(date) AS first, max(date) AS last
FROM sales
${where}`

const GROUP_KEY: Record<Exclude<Group, 'none'>, string> = { area: 'area', year: "strftime(date, '%Y')", type: 'type' }
const groupSql = (where: string, g: Exclude<Group, 'none'>) => `SELECT ${GROUP_KEY[g]} AS k, count(*) AS sales, median(price) AS price,
  median(price / sqft) FILTER (WHERE ${PPSF_OK}) AS ppsf
FROM sales
${g === 'area' ? and(where, 'area IS NOT NULL') : where}
GROUP BY 1`

const ORDER_SQL: Record<Order, string> = {
  new: 'date DESC, price DESC', old: 'date ASC, price ASC', priceDown: 'price DESC', priceUp: 'price ASC',
  ppsfUp: 'ppsf ASC NULLS LAST', big: 'sqft DESC NULLS LAST',
}
const listSql = (where: string, order: Order) => `SELECT date, address, area, zip, type, beds, sqft, price,
  CASE WHEN ${PPSF_OK} THEN round(price / sqft) END AS ppsf, year_built
FROM sales
${where}
ORDER BY ${ORDER_SQL[order]}`

function filtersFromHash(p: URLSearchParams): Filters {
  const n = (k: string) => { const v = p.get(k); return v && /^\d+$/.test(v) ? +v : null }
  const within = n('w')
  return {
    places: p.get('in')?.split('~').filter(Boolean) ?? [], lo: n('lo'), hi: n('hi'), beds: /^[1-5]$/.test(p.get('beds') ?? '') ? p.get('beds')! : '',
    types: p.get('t')?.split('~').filter(Boolean) ?? [], within: within != null && WITHIN.some((x) => x.m === within) ? within : 12, s1: n('s1'), s2: n('s2'),
  }
}
function viewFromHash(p: URLSearchParams): View {
  const pick = <T extends string>(k: string, ok: readonly T[], d: T) => (ok.includes(p.get(k) as T) ? (p.get(k) as T) : d)
  return {
    group: pick<Group>('g', ['none', 'area', 'year', 'type'], 'none'), metric: pick<Metric>('m', ['sales', 'price', 'ppsf'], 'sales'),
    asc: p.get('asc') === '1', order: pick<Order>('o', ORDERS.map((o) => o.id), 'new'),
  }
}
function stateToHash(f: Filters, v: View, mode: Mode, sql: string, scope: Scope | null, preset: string | null): URLSearchParams {
  const p = new URLSearchParams()
  if (f.places.length) p.set('in', f.places.join('~'))
  if (f.within !== 12) p.set('w', String(f.within))
  if (f.lo != null) p.set('lo', String(f.lo))
  if (f.hi != null) p.set('hi', String(f.hi))
  if (f.beds) p.set('beds', f.beds)
  if (f.types.length) p.set('t', f.types.join('~'))
  if (f.s1 != null) p.set('s1', String(f.s1))
  if (f.s2 != null) p.set('s2', String(f.s2))
  if (v.group !== 'none') p.set('g', v.group)
  if (v.metric !== 'sales') p.set('m', v.metric)
  if (v.asc) p.set('asc', '1')
  if (v.order !== 'new') p.set('o', v.order)
  if (mode === 'sql') p.set('sql', sql)
  else if (mode !== 'search') p.set('ask', mode)
  else if (preset) p.set('ask', preset)
  if (scope) p.set('data', scope)
  return p
}

// ---------- one-click questions ----------
type Fmt = 'usd' | 'num' | 'pct' | 'pctOf' | 'date' | 'text' | 'ppsf' | 'months'
interface Col { key: string; label: string; fmt: Fmt; bar?: boolean }
interface Ask {
  id: string; q: string; note: string
  /** answered by setting the filters, so the person can keep adjusting */
  preset?: { filters: Partial<Filters>; view: Partial<View> }
  sql?: (latest: string) => string
  stats?: { sql: (inner: string) => string; cols: Col[] }
  cols?: Col[]
  needs?: 'beds' | 'sqft'
  longer?: boolean   // reads better with the full history loaded
}
const L = (latest: string) => `DATE ${lit(latest)}`

const ASKS: Ask[] = [
  {
    id: 'under250', q: 'What sells under $250K, and where?', note: 'Last 12 months, by number sold.',
    preset: { filters: { hi: 250_000, within: 12 }, view: { group: 'area', metric: 'sales', asc: false } },
  },
  {
    id: 'cheap3bed', q: 'Where are 3-bed homes cheapest per sq ft?', note: 'Last 12 months, lowest $/sq ft first.', needs: 'beds',
    preset: { filters: { beds: '3', within: 12 }, view: { group: 'area', metric: 'ppsf', asc: true } },
  },
  {
    id: 'space400', q: 'Where does $400K buy 1,500+ sq ft?', note: 'Last 12 months, by number sold.', needs: 'sqft',
    preset: { filters: { hi: 400_000, s1: 1500, within: 12 }, view: { group: 'area', metric: 'sales', asc: false } },
  },
  {
    id: 'yearly', q: 'How have prices moved year by year?', note: 'Every year loaded.', longer: true,
    preset: { filters: { within: 0 }, view: { group: 'year', metric: 'price', asc: false } },
  },
  {
    id: 'flips', q: 'Homes sold twice within 2 years: how much did the price change?', note: 'Same property, 3 to 24 months apart, often bought cheap and resold after work. Co-ops left out.',
    sql: () => `WITH s AS (
  SELECT parcel, address, area, type, date, price,
    lag(date) OVER w AS first_date, lag(price) OVER w AS first_price
  FROM sales
  WHERE parcel IS NOT NULL AND type <> 'Co-op'
  WINDOW w AS (PARTITION BY parcel ORDER BY date, price)
)
SELECT address, area, type, first_date, first_price, date AS second_date, price AS second_price,
  round(date_diff('day', first_date, date) / 30.44) AS months,
  round((price / first_price - 1) * 100, 1) AS change_pct
FROM s
WHERE first_date IS NOT NULL
  AND date_diff('day', first_date, date) BETWEEN 90 AND 730
  AND first_price >= 10000 AND price >= 10000 AND price <> first_price
ORDER BY second_date DESC`,
    stats: {
      sql: (inner) => `SELECT count(*) AS homes, median(change_pct) AS typical_change, round(avg((change_pct > 0)::INT) * 100) AS rose_pct,
  median(second_price - first_price) AS typical_gain FROM (${inner})`,
      cols: [
        { key: 'homes', label: 'Homes sold twice', fmt: 'num' }, { key: 'typical_change', label: 'Typical change', fmt: 'pct' },
        { key: 'typical_gain', label: 'Typical $ change', fmt: 'usd' }, { key: 'rose_pct', label: 'Sold for more', fmt: 'pctOf' },
      ],
    },
    cols: [
      { key: 'address', label: 'Address', fmt: 'text' }, { key: 'change_pct', label: 'Change', fmt: 'pct' },
      { key: 'first_price', label: 'First price', fmt: 'usd' }, { key: 'second_price', label: 'Resold for', fmt: 'usd' },
      { key: 'second_date', label: 'Resold', fmt: 'date' }, { key: 'months', label: 'After', fmt: 'months' }, { key: 'area', label: '', fmt: 'text' },
    ],
  },
  {
    id: 'rising', q: 'Where did typical prices rise most this year?', note: 'Last 12 months vs the 12 before. 20+ sales in each. The mix of homes sold can move this.',
    sql: (latest) => `WITH t AS (
  SELECT area, price, date > ${L(latest)} - INTERVAL 12 MONTH AS now
  FROM sales
  WHERE area IS NOT NULL AND date > ${L(latest)} - INTERVAL 24 MONTH
)
SELECT area,
  round((median(price) FILTER (WHERE now) / median(price) FILTER (WHERE NOT now) - 1) * 100, 1) AS change_pct,
  median(price) FILTER (WHERE NOT now) AS before, median(price) FILTER (WHERE now) AS price,
  count(*) FILTER (WHERE now) AS sales
FROM t
GROUP BY area
HAVING count(*) FILTER (WHERE now) >= 20 AND count(*) FILTER (WHERE NOT now) >= 20
ORDER BY change_pct DESC`,
    cols: [
      { key: 'area', label: '', fmt: 'text' }, { key: 'change_pct', label: 'Change', fmt: 'pct', bar: true },
      { key: 'before', label: 'Year before', fmt: 'usd' }, { key: 'price', label: 'Last 12 mo', fmt: 'usd', bar: true }, { key: 'sales', label: 'Sold', fmt: 'num', bar: true },
    ],
  },
  {
    id: 'below', q: 'Recent sales at under half the area’s typical price', note: 'Last 3 months. Can include family sales, auctions and homes needing work.',
    sql: (latest) => `SELECT s.date, s.address, s.area, s.type, s.price, a.median_12m AS typical,
  round(s.price / a.median_12m * 100) AS pct_of_typical
FROM sales s
JOIN areas a USING (area)
WHERE s.date > ${L(latest)} - INTERVAL 3 MONTH
  AND s.price < 0.5 * a.median_12m
ORDER BY s.date DESC, s.price DESC`,
    cols: [
      { key: 'date', label: 'Sold', fmt: 'date' }, { key: 'address', label: 'Address', fmt: 'text' }, { key: 'area', label: '', fmt: 'text' },
      { key: 'type', label: 'Type', fmt: 'text' }, { key: 'price', label: 'Price', fmt: 'usd' }, { key: 'typical', label: 'Area typical', fmt: 'usd' },
      { key: 'pct_of_typical', label: 'Of typical', fmt: 'pctOf' },
    ],
  },
  {
    id: 'season', q: 'Do prices change with the season?', note: 'By month of sale, every year loaded.', longer: true,
    sql: () => `SELECT strftime(date, '%m') AS m, strftime(date, '%b') AS month, count(*) AS sales, median(price) AS price
FROM sales
GROUP BY 1, 2
ORDER BY 1`,
    cols: [{ key: 'month', label: 'Month', fmt: 'text' }, { key: 'price', label: 'Typical price', fmt: 'usd', bar: true }, { key: 'sales', label: 'Sold', fmt: 'num', bar: true }],
  },
]

// ---------- formatting ----------
const mb = (bytes: number) => `${(bytes / 1e6).toFixed(bytes < 1e7 ? 1 : 0)} MB`
const secs = (ms: number) => (ms < 1000 ? `${Math.round(ms)} ms` : `${(ms / 1000).toFixed(1)} s`)
const short = (d: string) => { const [y, m] = d.split('-'); return `${['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'][+m - 1]} ${y}` }
const signed = (v: number) => `${v > 0 ? '+' : v < 0 ? '−' : ''}${Math.abs(v).toFixed(1)}%`
function fmt(v: unknown, f: Fmt): string {
  if (v == null || v === '') return '–'
  switch (f) {
    case 'usd': return usd(v as number)
    case 'ppsf': return `${usd(v as number)}`
    case 'num': return num(v as number)
    case 'pct': return signed(v as number)
    case 'pctOf': return `${Math.round(v as number)}%`
    case 'months': return `${Math.round(v as number)} mo`
    case 'date': return /^\d{4}-\d{2}-\d{2}$/.test(String(v)) ? dateLabel(String(v)) : String(v)
    default: return String(v)
  }
}
/** a best guess for columns of hand-written SQL */
function guessFmt(name: string, kind: string): Fmt {
  const n = name.toLowerCase()
  if (kind === 'date') return 'date'
  if (kind !== 'num') return 'text'
  if (/pct|percent|yield/.test(n)) return 'pct'
  if (/year|decade|zip|beds|parcel|lat|lon|(^|_)id$/.test(n)) return 'text'
  if (/price|median|avg|mean|min|max|assessed|ppsf|rent|usd|value|typical/.test(n)) return 'usd'
  return 'num'
}
const smallDevice = () => {
  const mem = (navigator as Navigator & { deviceMemory?: number }).deviceMemory
  return window.matchMedia('(max-width: 760px)').matches || (mem != null && mem <= 4)
}

type LoadState =
  | { phase: 'loading'; progress: Progress; started: number }
  | { phase: 'ready'; loaded: Loaded; latest: string }
  | { phase: 'error'; message: string; memory: boolean }

type Mode = 'search' | 'sql' | string   // a string other than these is an Ask id
interface Summary { n: number; med: number | null; p25: number | null; p75: number | null; ppsf: number | null; sized: number; first: string | null; last: string | null }
interface GroupRow { k: string; sales: number; price: number | null; ppsf: number | null }
interface Answer { ask: Ask | null; result: Result; stats: Result | null }

export default function Explore({ city }: { city: City }) {
  const { data: manifest, error: manifestError } = useData<QueryManifest>(`${city.id}/query.json`)
  const initial = useRef(readHash()).current
  const duck = useRef<Duck | null>(null)
  const [scope, setScope] = useState<Scope | null>(null)
  const [attempt, setAttempt] = useState(0)
  const [load, setLoad] = useState<LoadState>({ phase: 'loading', progress: { stage: 'engine' }, started: performance.now() })
  const [filters, setFilters] = useState<Filters>(() => filtersFromHash(initial))
  const [view, setView] = useState<View>(() => viewFromHash(initial))
  const [page, setPage] = useState(0)
  // A query in a shared link is shown in the SQL box but never runs by itself: the visitor reads it and presses Run.
  const [shared, setShared] = useState(!!initial.get('sql'))
  const [mode, setMode] = useState<Mode>(() => (initial.get('sql') ? 'search' : ASKS.find((a) => a.id === initial.get('ask') && a.sql)?.id ?? 'search'))
  const [preset, setPreset] = useState<string | null>(() => ASKS.find((a) => a.id === initial.get('ask') && a.preset)?.id ?? null)
  const [sql, setSql] = useState(initial.get('sql') ?? '')
  const [seed, setSeed] = useState(initial.get('sql') ?? '')   // what the SQL box starts from
  const [sqlOpen, setSqlOpen] = useState(!!initial.get('sql'))
  const [drawer, setDrawer] = useState(false)
  const [options, setOptions] = useState<{ places: { name: string; n: number; zip: boolean }[]; types: string[]; beds: number; sqft: number }>({ places: [], types: [], beds: 0, sqft: 0 })

  const [summary, setSummary] = useState<Summary | null>(null)
  const [groups, setGroups] = useState<GroupRow[] | null>(null)
  const [list, setList] = useState<Result | null>(null)
  const [answer, setAnswer] = useState<Answer | null>(null)
  const [busy, setBusy] = useState<{ since: number } | null>(null)
  const [slow, setSlow] = useState(false)
  const [queryError, setQueryError] = useState<string | null>(null)
  const [took, setTook] = useState<number | null>(null)
  const [copied, setCopied] = useState(false)
  const token = useRef(0)

  // ---- which file to load: Parquet over HTTP when published, else CSV; phones default to the last few years
  useEffect(() => {
    if (!manifest || scope) return
    const asked = initial.get('data') as Scope | null
    const ok = (s: Scope | null): s is Scope => s === 'full' || s === 'recent' || (s === 'parquet' && !!manifest.parquet)
    setScope(ok(asked) ? asked : manifest.parquet ? 'parquet' : smallDevice() ? 'recent' : 'full')
  }, [manifest, scope, initial])

  useEffect(() => {
    if (!manifest || !scope) return
    let live = true
    const started = performance.now()
    setLoad({ phase: 'loading', progress: { stage: 'engine' }, started })
    setSummary(null); setGroups(null); setList(null); setAnswer(null)
    ;(async () => {
      try {
        duck.current = duck.current ?? await import('../lib/duck')
        const loaded = await duck.current.loadCity(city.id, manifest, scope, (progress) => live && setLoad({ phase: 'loading', progress, started }))
        if (!live) return
        const d = duck.current
        const latest = await d.run(loaded.conn, "SELECT strftime(max(date), '%Y-%m-%d') FROM sales")
        const [a, z, t, n] = [
          await d.run(loaded.conn, 'SELECT area, count(*) FROM sales WHERE area IS NOT NULL GROUP BY 1 ORDER BY 2 DESC', 5000),
          await d.run(loaded.conn, 'SELECT zip, count(*) FROM sales WHERE zip IS NOT NULL GROUP BY 1 ORDER BY 2 DESC', 5000),
          await d.run(loaded.conn, 'SELECT type FROM sales GROUP BY type ORDER BY count(*) DESC'),
          await d.run(loaded.conn, `SELECT avg((beds IS NOT NULL)::INT), avg((${PPSF_OK})::INT) FROM sales`),
        ]
        if (!live) return
        setOptions({
          places: [...a.rows.map((r) => ({ name: String(r[0]), n: Number(r[1]), zip: false })), ...z.rows.map((r) => ({ name: String(r[0]), n: Number(r[1]), zip: true }))],
          types: t.rows.map((r) => String(r[0])), beds: Number(n.rows[0]?.[0] ?? 0), sqft: Number(n.rows[0]?.[1] ?? 0),
        })
        setLoad({ phase: 'ready', loaded, latest: String(latest.rows[0]?.[0] ?? manifest.latestSale) })
      } catch (e) {
        if (!live) return
        duck.current?.reset()
        const msg = String((e as Error)?.message || e)
        const memory = duck.current?.isMemoryError(msg) ?? /memory/i.test(msg)
        setLoad({ phase: 'error', memory, message: memory ? 'This device ran out of memory loading the sales.' : /gzip|csv|parse|conversion/i.test(msg) ? 'The sales file arrived damaged or incomplete.' : msg.split('\n')[0].slice(0, 200) })
      }
    })()
    return () => { live = false }
  }, [manifest, scope, city.id, attempt])

  const ready = load.phase === 'ready' ? load : null
  const conn = ready?.loaded.conn ?? null
  const latest = ready?.latest ?? ''
  const where = useMemo(() => (latest ? whereSql(filters, latest) : ''), [filters, latest])

  // keep the link in step with what is on screen
  useEffect(() => { writeHash(stateToHash(filters, view, shared && mode === 'search' ? 'sql' : mode, sql, scope, preset)) }, [filters, view, mode, sql, scope, preset, shared])

  // lock the page behind the phone drawer
  useEffect(() => {
    if (!drawer) return
    const prev = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => { document.body.style.overflow = prev }
  }, [drawer])

  // a query running more than a moment shows Cancel
  useEffect(() => {
    if (!busy) { setSlow(false); return }
    const t = setTimeout(() => setSlow(true), 400)
    return () => clearTimeout(t)
  }, [busy])

  async function guarded<T>(fn: (d: Duck, c: NonNullable<typeof conn>, my: number) => Promise<T>) {
    if (!conn || !duck.current) return
    const my = ++token.current
    const t0 = performance.now()
    setBusy({ since: t0 })
    setQueryError(null)
    try {
      await fn(duck.current, conn, my)
      if (my === token.current) setTook(performance.now() - t0)
    } catch (e) {
      if (my !== token.current) return
      const msg = String((e as Error)?.message || e)
      setQueryError(/interrupt|cancel/i.test(msg) ? 'Stopped.' : duck.current.isMemoryError(msg) ? 'Ran out of memory on this query. Try a narrower search or the last 3 years.' : msg)
    } finally {
      if (my === token.current) setBusy(null)
    }
  }

  // ---- the search: summary, optional group table, one page of sales
  useEffect(() => {
    if (!conn || mode !== 'search') return
    const t = setTimeout(() => guarded(async (d, c, my) => {
      const s = await d.run(c, summarySql(where), 1)
      if (my !== token.current) return
      const r = s.rows[0] ?? []
      setSummary({ n: Number(r[0] ?? 0), med: r[1] as number | null, p25: r[2] as number | null, p75: r[3] as number | null, ppsf: r[4] as number | null, sized: Number(r[5] ?? 0), first: r[6] as string | null, last: r[7] as string | null })
      if (view.group !== 'none') {
        const g = await d.run(c, groupSql(where, view.group), 5000)
        if (my !== token.current) return
        setGroups(g.rows.map((x) => ({ k: String(x[0]), sales: Number(x[1]), price: x[2] as number | null, ppsf: x[3] as number | null })))
      } else setGroups(null)
      const l = await d.run(c, `${listSql(where, view.order)}\nLIMIT ${PAGE} OFFSET ${page * PAGE}`, PAGE)
      if (my !== token.current) return
      setList(l)
    }), 120)
    return () => clearTimeout(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [conn, mode, where, view.group, view.order, page])

  // ---- a question, or hand-written SQL
  useEffect(() => {
    if (!conn || mode === 'search') return
    const ask = ASKS.find((a) => a.id === mode) ?? null
    const text = mode === 'sql' ? sql : ask?.sql?.(latest) ?? ''
    if (!text.trim()) return
    setAnswer(null)
    guarded(async (d, c, my) => {
      const result = await d.run(c, text, 1000)
      const stats = ask?.stats ? await d.run(c, ask.stats.sql(text), 1) : null
      if (my === token.current) setAnswer({ ask, result, stats })
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [conn, mode, latest, mode === 'sql' ? sql : ''])

  const patch = (p: Partial<Filters>) => { setFilters((f) => ({ ...f, ...p })); setPage(0); setPreset(null); setMode('search') }
  const patchView = (p: Partial<View>) => { setView((v) => ({ ...v, ...p })); if (p.order || p.group) setPage(0); setMode('search') }

  function ask(a: Ask) {
    setDrawer(false)
    if (a.preset) {
      setFilters({ ...NO_FILTERS, ...a.preset.filters })
      setView({ ...DEFAULT_VIEW, ...a.preset.view })
      setPage(0)
      setPreset(a.id)
      setMode('search')
    } else {
      setMode(a.id)
      setPreset(null)
      setSeed(a.sql!(latest))
    }
    document.querySelector('.ex-results')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }

  async function downloadCsv() {
    if (!conn || !duck.current) return
    let text: string | null = null
    let name = `realist-${city.id}-sales.csv`
    if (mode === 'search') {
      const r = await duck.current.run(conn, `SELECT date, price, type, address, zip, area, sqft, beds, year_built FROM sales\n${where}\nORDER BY ${ORDER_SQL[view.order].replace('ppsf', 'CASE WHEN ' + PPSF_OK + ' THEN price / sqft END')}`, 0)
      text = r.csv()
    } else if (answer) {
      text = answer.result.csv()
      name = `realist-${city.id}-${answer.ask?.id ?? 'query'}.csv`
    }
    if (text == null) return
    const url = URL.createObjectURL(new Blob([text], { type: 'text/csv;charset=utf-8' }))
    const a = document.createElement('a')
    a.href = url
    a.download = name
    document.body.appendChild(a)
    a.click()
    a.remove()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  }

  async function copyLink() {
    try { await navigator.clipboard.writeText(window.location.href) } catch { /* the address bar has it */ }
    setCopied(true)
    setTimeout(() => setCopied(false), 1600)
  }

  const cancel = () => { if (conn) duck.current?.cancel(conn) }

  if (manifestError) return <div className="notice bad">The sales files for {city.name} are not published yet.</div>
  if (!manifest || !scope) return <div className="loading">Loading…</div>

  const noun = city.areaNoun
  const nounPlural = `${noun.toLowerCase()}s`.replace(/ys$/, 'ies')
  const activeCount = [filters.places.length > 0, filters.lo != null || filters.hi != null, !!filters.beds, filters.types.length > 0, filters.s1 != null || filters.s2 != null].filter(Boolean).length
  const recentOnly = scope === 'recent'
  const wantsMore = recentOnly && (filters.within === 0 || filters.within > manifest.recent.years * 12)
  const activeAsk = ASKS.find((a) => a.id === (mode === 'search' ? preset : mode))
  const loadFull = () => setScope(manifest.parquet ? 'parquet' : 'full')

  const filterPanel = (
    <FilterPanel
      city={city} filters={filters} options={options} patch={patch} disabled={!ready}
      recentYears={recentOnly ? manifest.recent.years : null}
      onClose={() => setDrawer(false)} count={mode === 'search' ? summary?.n ?? null : null}
      onReset={() => { setFilters(NO_FILTERS); setView(DEFAULT_VIEW); setPage(0); setPreset(null); setMode('search') }} activeCount={activeCount}
    />
  )

  return (
    <div className="explore">
      <LoadBar load={load} scope={scope} manifest={manifest} setScope={setScope} retry={() => setAttempt((n) => n + 1)} />

      <section className="ex-asks" aria-label="Quick questions">
        <h3>Quick questions</h3>
        <div className="ex-ask-list">
          {ASKS.filter((a) => !ready || (a.needs === 'beds' ? options.beds >= 0.2 : a.needs === 'sqft' ? options.sqft >= 0.2 : true)).map((a) => {
            const on = activeAsk?.id === a.id
            return (
              <button key={a.id} className={`ex-ask ${on ? 'on' : ''}`} aria-pressed={on} disabled={!ready} onClick={() => ask(a)}>
                <span>{a.q}</span><i aria-hidden="true">→</i>
              </button>
            )
          })}
        </div>
      </section>

      <div className={`ex-layout ${drawer ? 'drawer-open' : ''}`}>
        <aside className="ex-filters" aria-label="Filters">{filterPanel}</aside>
        {drawer && <div className="ex-scrim" onClick={() => setDrawer(false)} />}

        <section className="ex-results" aria-live="polite">
          <div className="ex-toolbar">
            <button className="ex-filter-btn" onClick={() => setDrawer(true)}>
              Filters{activeCount ? <b>{activeCount}</b> : null}
            </button>
            <div className="ex-tools">
              {busy && slow && <button className="ghost small" onClick={cancel}>Cancel</button>}
              <button className="ghost small" onClick={copyLink}>{copied ? 'Link copied' : 'Copy link'}</button>
              <button className="ghost small" onClick={downloadCsv} disabled={!ready || (mode !== 'search' && !answer)}>Download CSV</button>
            </div>
          </div>

          {queryError && (
            <div className="ex-error card"><b>{queryError === 'Stopped.' ? 'Stopped.' : 'That did not work.'}</b>
              {queryError !== 'Stopped.' && <pre>{queryError}</pre>}
              {queryError === 'Stopped.' && <span> Change a filter or pick a question to search again.</span>}
            </div>
          )}

          {!ready && <div className="card ex-wait">{load.phase === 'error' ? 'Results appear once the sales load.' : 'Getting the sales ready…'}</div>}

          {ready && mode === 'search' && (
            <SearchResults
              city={city} noun={noun} nounPlural={nounPlural} filters={filters} view={view} patchView={patchView} patch={patch}
              summary={summary} groups={groups} list={list} page={page} setPage={setPage} busy={!!busy}
              ask={activeAsk ?? null} wantsMore={wantsMore} loadFull={loadFull} recentYears={manifest.recent.years}
              full={manifest.full.bytes} sizeShare={options.sqft} bedShare={options.beds}
            />
          )}

          {ready && mode !== 'search' && (
            <AnswerView
              answer={answer} busy={!!busy} ask={activeAsk ?? null} isSql={mode === 'sql'} noun={noun}
              back={() => setMode('search')} recentOnly={recentOnly} recentYears={manifest.recent.years} loadFull={loadFull}
            />
          )}
          {ready && took != null && !busy && !queryError && <p className="ex-took">Answered in {secs(took)}, on your device.</p>}
        </section>
      </div>

      <details className="card ex-advanced" open={sqlOpen} onToggle={(e) => { if (e.target === e.currentTarget) setSqlOpen(e.currentTarget.open) }}>
        <summary><b>Advanced: write SQL</b><span className="muted">For people who know SQL. Runs on a read-only copy in your browser.</span></summary>
        <div className="ex-sql">
          <div className="ex-editor-wrap">
            <div className="ex-actions">
              <button className="ghost small" disabled={!ready} onClick={() => setSeed(mode === 'search' ? listSql(where, view.order) : activeAsk?.sql?.(latest) ?? sql)}>Start from what is on screen</button>
            </div>
            <SqlBox initial={seed} disabled={!ready} busy={!!busy}
              onRun={(text) => { setShared(false); setSql(text); setPreset(null); setMode('sql') }} onCancel={cancel} />
            {shared && <p className="muted">This link came with a query. Read it, then press Run.</p>}
          </div>
          <Schema noun={noun} />
        </div>
      </details>

      <p className="footnote">Recorded sales only: no asking prices. “Typical” means the median. Prices are as recorded and not adjusted for inflation. Everything runs on your device; nothing is sent anywhere.</p>
    </div>
  )
}

// ---------- loading status ----------
function LoadBar({ load, scope, manifest, setScope, retry }: {
  load: LoadState; scope: Scope; manifest: QueryManifest; setScope: (s: Scope) => void; retry: () => void
}) {
  const [now, setNow] = useState(performance.now())
  useEffect(() => {
    if (load.phase !== 'loading') return
    const t = setInterval(() => setNow(performance.now()), 500)
    return () => clearInterval(t)
  }, [load.phase])
  const csvChoice = !manifest.parquet || scope !== 'parquet'
  const info = scope === 'full' ? manifest.full : manifest.recent
  const choice = csvChoice && (
    <div className="segmented ex-scope" role="group" aria-label="Sales loaded">
      <button className={scope === 'recent' ? 'on' : ''} aria-pressed={scope === 'recent'} disabled={load.phase === 'loading'} onClick={() => setScope('recent')}>
        Last {manifest.recent.years} years <em>{mb(manifest.recent.bytes)}</em></button>
      <button className={scope === 'full' ? 'on' : ''} aria-pressed={scope === 'full'} disabled={load.phase === 'loading'} onClick={() => setScope('full')}>
        Since {manifest.full.from?.slice(0, 4)} <em>{mb(manifest.full.bytes)}</em></button>
    </div>
  )

  if (load.phase === 'error') {
    return (
      <section className="card ex-status ex-status-error" role="alert">
        <div className="ex-status-main">
          <b>Could not load the sales.</b>
          <span>{load.message}</span>
        </div>
        <div className="ex-status-actions">
          <button className="ex-run" onClick={retry}>Try again</button>
          {scope !== 'recent' && <button className="ghost small" onClick={() => setScope('recent')}>Load only the last {manifest.recent.years} years ({mb(manifest.recent.bytes)})</button>}
        </div>
      </section>
    )
  }
  if (load.phase === 'loading') {
    const p = load.progress
    const elapsed = (now - load.started) / 1000
    const steps = [
      { id: 'engine', label: 'Start engine' },
      { id: 'download', label: scope === 'parquet' ? 'Connect' : `Download ${mb(info.bytes)}` },
      { id: 'table', label: 'Prepare' },
    ]
    const at = steps.findIndex((s) => s.id === p.stage)
    const share = p.total ? Math.min(100, Math.round(((p.loaded ?? 0) / p.total) * 100)) : null
    const overall = Math.round(((at + (share ?? (p.stage === 'table' ? 50 : 10)) / 100) / steps.length) * 100)
    return (
      <section className="card ex-status" aria-live="polite">
        <div className="ex-status-main">
          <b>Loading {num(info.rows)} sales…</b>
          <div className="bar-track ex-progress" role="progressbar" aria-valuenow={overall} aria-valuemin={0} aria-valuemax={100}><div className="bar-fill" style={{ width: `${overall}%` }} /></div>
          <ol className="ex-steps">
            {steps.map((s, i) => <li key={s.id} className={i < at ? 'done' : i === at ? 'now' : ''}>{s.label}{i === at && share != null ? ` ${share}%` : ''}</li>)}
          </ol>
          <span className="muted">{Math.floor(elapsed)} s{elapsed > 25 ? ' · slower than usual' : ''}</span>
        </div>
        {choice}
      </section>
    )
  }
  const l = load.loaded
  return (
    <section className="card ex-status">
      <div className="ex-status-main">
        <b>{num(l.rows)} sales</b>
        <span className="muted">{scope === 'parquet' ? 'full history' : `${short(info.from!)} to ${short(load.latest)}`} · ready in {secs(l.ms.engine + l.ms.download + l.ms.table)}</span>
      </div>
      {choice}
    </section>
  )
}

// ---------- filters ----------
type PlaceOpt = { name: string; n: number; zip: boolean }
function FilterPanel({ city, filters: f, options, patch, disabled, recentYears, onClose, count, onReset, activeCount }: {
  city: City; filters: Filters; options: { places: PlaceOpt[]; types: string[]; beds: number; sqft: number }
  patch: (p: Partial<Filters>) => void; disabled: boolean; recentYears: number | null
  onClose: () => void; count: number | null; onReset: () => void; activeCount: number
}) {
  const types = options.types.length ? options.types : ['Single family', 'Multi-family']
  return (
    <div className="ex-panel">
      <div className="ex-panel-head">
        <h3>Search sold homes</h3>
        <button className="ex-close" onClick={onClose} aria-label="Close filters">×</button>
      </div>

      <div className="ex-f">
        <span className="ex-f-label">Where</span>
        <PlaceSearch noun={city.areaNoun} options={options.places} value={f.places} onChange={(places) => patch({ places })} />
      </div>

      <div className="ex-f">
        <span className="ex-f-label">Sold in the last</span>
        <div className="segmented ex-seg" role="group" aria-label="Sold within">
          {WITHIN.map((w) => (
            <button key={w.m} className={f.within === w.m ? 'on' : ''} aria-pressed={f.within === w.m} onClick={() => patch({ within: w.m })}
              title={recentYears && (w.m === 0 || w.m > recentYears * 12) ? `Only ${recentYears} years are loaded` : undefined}>{w.label}</button>
          ))}
        </div>
      </div>

      <div className="ex-f">
        <span className="ex-f-label">Price</span>
        <div className="ex-pair">
          <NumSelect label="Min price" value={f.lo} values={PRICES} format={usdCompact} onChange={(lo) => patch({ lo })} placeholder="No min" />
          <span className="muted">to</span>
          <NumSelect label="Max price" value={f.hi} values={PRICES} format={usdCompact} onChange={(hi) => patch({ hi })} placeholder="No max" />
        </div>
      </div>

      {(options.beds > 0 || f.beds !== '' || !options.types.length) && <div className="ex-f">
        <span className="ex-f-label">Beds</span>
        <div className="segmented ex-seg" role="group" aria-label="Bedrooms">
          {['', '1', '2', '3', '4', '5'].map((b) => (
            <button key={b} className={f.beds === b ? 'on' : ''} aria-pressed={f.beds === b} onClick={() => patch({ beds: b })}>{b === '' ? 'Any' : b === '5' ? '5+' : b}</button>
          ))}
        </div>
        {f.beds && options.beds > 0 && options.beds < 0.9 && <small className="muted">Beds recorded for {Math.round(options.beds * 100)}% of sales; the rest are left out.</small>}
      </div>}

      <div className="ex-f">
        <span className="ex-f-label">Home type</span>
        <div className="ex-chips">
          {types.map((t) => {
            const on = f.types.includes(t)
            return <button key={t} className={`ex-chip ${on ? 'on' : ''}`} aria-pressed={on}
              onClick={() => patch({ types: on ? f.types.filter((x) => x !== t) : [...f.types, t] })}>{t}</button>
          })}
        </div>
      </div>

      <div className="ex-f">
        <span className="ex-f-label">Size (sq ft)</span>
        <div className="ex-pair">
          <NumSelect label="Min size" value={f.s1} values={SIZES} format={num} onChange={(s1) => patch({ s1 })} placeholder="No min" />
          <span className="muted">to</span>
          <NumSelect label="Max size" value={f.s2} values={SIZES} format={num} onChange={(s2) => patch({ s2 })} placeholder="No max" />
        </div>
        {(f.s1 != null || f.s2 != null) && options.sqft > 0 && options.sqft < 0.9 && <small className="muted">Size recorded for {Math.round(options.sqft * 100)}% of sales; the rest are left out.</small>}
      </div>

      <div className="ex-panel-foot">
        <button className="ghost small" onClick={onReset} disabled={disabled || (activeCount === 0 && f.within === 12)}>Clear all</button>
        <button className="ex-run ex-show" onClick={onClose}>{count == null ? 'Show results' : `Show ${num(count)} home${count === 1 ? '' : 's'}`}</button>
      </div>
    </div>
  )
}

function NumSelect({ label, value, values, format, onChange, placeholder }: {
  label: string; value: number | null; values: number[]; format: (n: number) => string; onChange: (v: number | null) => void; placeholder: string
}) {
  const all = value != null && !values.includes(value) ? [...values, value].sort((a, b) => a - b) : values
  return (
    <select className="ex-select" aria-label={label} value={value ?? ''} onChange={(e) => onChange(e.target.value ? +e.target.value : null)}>
      <option value="">{placeholder}</option>
      {all.map((v) => <option key={v} value={v}>{format(v)}</option>)}
    </select>
  )
}

function PlaceSearch({ noun, options, value, onChange }: { noun: string; options: PlaceOpt[]; value: string[]; onChange: (v: string[]) => void }) {
  const [q, setQ] = useState('')
  const [open, setOpen] = useState(false)
  const [hi, setHi] = useState(0)
  const matches = useMemo(() => {
    const s = q.trim().toLowerCase()
    const pool = options.filter((o) => !value.includes(o.name) && (s ? true : !o.zip))
    if (!s) return pool.slice(0, 8)
    const starts = pool.filter((o) => o.name.toLowerCase().startsWith(s) || o.name.toLowerCase().split(/[\s-]+/).some((w) => w.startsWith(s)))
    const inside = pool.filter((o) => !starts.includes(o) && o.name.toLowerCase().includes(s))
    return [...starts, ...inside].slice(0, 8)
  }, [q, options, value])
  const add = (name: string) => { onChange([...value, name]); setQ(''); setHi(0); setOpen(false) }
  return (
    <div className="ex-place">
      <input type="search" className="ex-place-input" placeholder={`${noun} or ZIP`} value={q} aria-label={`${noun} or ZIP`}
        role="combobox" aria-expanded={open && matches.length > 0} aria-autocomplete="list"
        onChange={(e) => { setQ(e.target.value); setOpen(true); setHi(0) }}
        onFocus={() => setOpen(true)} onBlur={() => setTimeout(() => setOpen(false), 150)}
        onKeyDown={(e) => {
          if (e.key === 'ArrowDown') { e.preventDefault(); setHi((h) => Math.min(matches.length - 1, h + 1)) }
          else if (e.key === 'ArrowUp') { e.preventDefault(); setHi((h) => Math.max(0, h - 1)) }
          else if (e.key === 'Enter' && matches[hi]) { e.preventDefault(); add(matches[hi].name) }
          else if (e.key === 'Escape') setOpen(false)
          else if (e.key === 'Backspace' && !q && value.length) onChange(value.slice(0, -1))
        }} />
      {open && matches.length > 0 && (
        <ul className="ex-place-pop" role="listbox">
          {!q && <li className="ex-place-hint" aria-hidden="true">Most sales</li>}
          {matches.map((m, i) => (
            <li key={m.name} role="option" aria-selected={i === hi} className={i === hi ? 'hi' : ''}
              onMouseDown={(e) => { e.preventDefault(); add(m.name) }} onMouseEnter={() => setHi(i)}>
              <span>{m.zip ? `ZIP ${m.name}` : m.name}</span><em>{num(m.n)}</em>
            </li>
          ))}
        </ul>
      )}
      {open && q && !matches.length && options.length > 0 && <div className="ex-place-pop ex-place-none">No match</div>}
      {value.length > 0 && (
        <div className="ex-chips ex-picked">
          {value.map((v) => (
            <button key={v} className="ex-chip on" onClick={() => onChange(value.filter((x) => x !== v))} aria-label={`Remove ${v}`}>
              {isZip(v) ? `ZIP ${v}` : v} <span aria-hidden="true">×</span></button>
          ))}
        </div>
      )}
    </div>
  )
}

// ---------- search results ----------
function describe(f: Filters, nounPlural: string): string {
  const parts: string[] = []
  if (f.places.length === 1) parts.push(`in ${isZip(f.places[0]) ? `ZIP ${f.places[0]}` : f.places[0]}`)
  else if (f.places.length > 1) parts.push(`in ${f.places.length} places`)
  else parts.push(`across all ${nounPlural}`)
  const w = WITHIN.find((x) => x.m === f.within)
  parts.push(f.within === 0 ? 'in every year loaded' : `in the last ${w?.label.replace('mo', 'months').replace('yr', f.within === 12 ? 'year' : 'years').replace(/^1 year/, 'year')}`)
  return parts.join(' ')
}

function SearchResults({ city, noun, nounPlural, filters, view, patchView, patch, summary, groups, list, page, setPage, busy, ask, wantsMore, loadFull, recentYears, full, sizeShare, bedShare }: {
  city: City; noun: string; nounPlural: string; filters: Filters; view: View; patchView: (p: Partial<View>) => void; patch: (p: Partial<Filters>) => void
  summary: Summary | null; groups: GroupRow[] | null; list: Result | null; page: number; setPage: (p: number) => void; busy: boolean
  ask: Ask | null; wantsMore: boolean; loadFull: () => void; recentYears: number; full: number; sizeShare: number; bedShare: number
}) {
  const s = summary
  const pages = s ? Math.max(1, Math.ceil(s.n / PAGE)) : 1
  return (
    <div className={`ex-search ${busy ? 'is-busy' : ''}`}>
      {ask && <div className="ex-asked"><b>{ask.q}</b> <span className="muted">{ask.note} Adjust the filters to refine.</span></div>}
      {wantsMore && (
        <div className="notice ex-more">Only the last {recentYears} years are loaded. <button className="ghost small" onClick={loadFull}>Load full history ({mb(full)})</button></div>
      )}
      <div className="ex-headline">
        {s ? <><b>{num(s.n)}</b> home{s.n === 1 ? '' : 's'} sold {describe(filters, nounPlural)}</> : 'Searching…'}
      </div>
      <div className="ex-tiles">
        <Tile label="Typical price" value={s?.med != null ? usdCompact(s.med) : '–'} foot="median sold price" />
        <Tile label="Most sold between" small value={s?.p25 != null ? `${usdCompact(s.p25)}–${usdCompact(s.p75)}` : '–'} foot="middle half of sales" />
        <Tile label="Typical $/sq ft" value={s?.ppsf != null ? usd(s.ppsf) : '–'}
          foot={s && s.n ? (s.sized < s.n * 0.9 ? `${Math.round((s.sized / s.n) * 100)}% have a size on record` : 'where size is known') : ''} />
        <Tile label="Homes sold" value={s ? num(s.n) : '–'} foot={s?.first ? `${short(s.first)} to ${short(s.last!)}` : ''} />
      </div>
      {s && s.n === 0 && <div className="notice">No sales match. Try a wider price range or a longer period.</div>}

      <div className="ex-groupbar">
        <span className="ex-f-label">Break down by</span>
        <div className="segmented" role="group" aria-label="Break down by">
          {(['none', 'area', 'year', 'type'] as Group[]).map((g) => (
            <button key={g} className={view.group === g ? 'on' : ''} aria-pressed={view.group === g} onClick={() => patchView({ group: g })}>
              {g === 'none' ? 'Nothing' : g === 'area' ? noun : g === 'year' ? 'Year' : 'Home type'}</button>
          ))}
        </div>
      </div>

      {view.group !== 'none' && groups && s && s.n > 0 && (
        <GroupTable rows={groups} group={view.group} view={view} patchView={patchView} noun={noun} nounPlural={nounPlural}
          pick={view.group === 'area' ? (k) => patch({ places: [k] }) : view.group === 'type' ? (k) => patch({ types: [k] }) : null}
          sizeShare={sizeShare} />
      )}

      {s && s.n > 0 && list && (
        <div className="card ex-list-card">
          <header className="ex-list-head">
            <h3>The sales</h3>
            <label className="ex-sort"><span className="muted">Sort</span>
              <select className="ex-select" value={view.order} onChange={(e) => patchView({ order: e.target.value as Order })}>
                {ORDERS.filter((o) => (o.id === 'ppsfUp' || o.id === 'big' ? sizeShare > 0.2 : true)).map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
              </select>
            </label>
          </header>
          <SalesList result={list} showBeds={bedShare > 0.2} cityId={city.id} />
          <div className="ex-pager">
            <button className="ghost small" disabled={page === 0} onClick={() => setPage(page - 1)}>← Prev</button>
            <span className="muted">{num(page * PAGE + 1)}–{num(Math.min(s.n, (page + 1) * PAGE))} of {num(s.n)}</span>
            <button className="ghost small" disabled={page + 1 >= pages} onClick={() => setPage(page + 1)}>Next →</button>
          </div>
        </div>
      )}
    </div>
  )
}

function Tile({ label, value, foot, small }: { label: string; value: string; foot: string; small?: boolean }) {
  return <div className={`ex-tile ${small ? 'small' : ''}`}><div className="ex-tile-label">{label}</div><div className="ex-tile-value">{value}</div><div className="ex-tile-foot">{foot}</div></div>
}

const METRICS: { id: Metric; label: string }[] = [{ id: 'sales', label: 'Homes sold' }, { id: 'price', label: 'Typical price' }, { id: 'ppsf', label: '$/sq ft' }]

function GroupTable({ rows, group, view, patchView, noun, nounPlural, pick, sizeShare }: {
  rows: GroupRow[]; group: Exclude<Group, 'none'>; view: View; patchView: (p: Partial<View>) => void
  noun: string; nounPlural: string; pick: ((k: string) => void) | null; sizeShare: number
}) {
  const [all, setAll] = useState(false)
  const m = view.metric
  const val = (r: GroupRow) => (m === 'sales' ? r.sales : m === 'price' ? r.price : r.ppsf)
  const ranked = m === 'sales' || group === 'year' ? rows : rows.filter((r) => r.sales >= MIN_RANKED && val(r) != null)
  const left = rows.length - ranked.length
  const sorted = [...ranked].sort((a, b) => {
    if (group === 'year') return a.k < b.k ? (view.asc ? -1 : 1) : a.k > b.k ? (view.asc ? 1 : -1) : 0
    return ((val(a) ?? 0) - (val(b) ?? 0)) * (view.asc ? 1 : -1)
  })
  const top = Math.max(1, ...sorted.map((r) => val(r) ?? 0))
  const LIMIT = 15
  const shown = all ? sorted : sorted.slice(0, LIMIT)
  const label = { area: noun, year: 'Year', type: 'Home type' }[group]
  const f = (v: number | null) => (m === 'sales' ? num(v) : m === 'price' ? usdCompact(v) : usd(v))
  return (
    <div className="card ex-group">
      <header className="ex-list-head">
        <div className="segmented" role="group" aria-label="Rank by">
          {METRICS.filter((x) => x.id !== 'ppsf' || sizeShare > 0.2).map((x) => (
            <button key={x.id} className={m === x.id ? 'on' : ''} aria-pressed={m === x.id} onClick={() => patchView({ metric: x.id })}>{x.label}</button>
          ))}
        </div>
        <button className="ghost small" onClick={() => patchView({ asc: !view.asc })}>
          {group === 'year' ? (view.asc ? 'Oldest first' : 'Newest first') : view.asc ? 'Lowest first' : 'Highest first'} ⇅</button>
      </header>
      <div className="ex-grow ex-grow-head" aria-hidden="true">
        <span>{label}</span><span>{METRICS.find((x) => x.id === m)!.label}</span><span className="n">Sold</span><span className="n">Typical</span><span className="n">$/sq ft</span>
      </div>
      <ul className={`ex-glist dup-${m}`}>
        {shown.map((r) => {
          const v = val(r)
          return (
            <li key={r.k} className="ex-grow">
              {pick ? <button className="ex-gname" onClick={() => pick(r.k)} title={`Show only ${r.k}`}>{r.k}</button> : <span className="ex-gname">{r.k}</span>}
              <span className="ex-gbar" title={`${r.k}: ${f(v)}`}>
                <span className="ex-gtrack"><span className="ex-gfill" style={{ width: `${v == null ? 0 : Math.max(1.5, (v / top) * 100)}%` }} /></span>
                <b>{f(v)}</b>
              </span>
              <span className="n ex-gsub g-sales" data-l="sold">{num(r.sales)}</span>
              <span className="n ex-gsub g-price" data-l="typical">{usdCompact(r.price)}</span>
              <span className="n ex-gsub g-ppsf" data-l="/sq ft">{r.ppsf == null ? '–' : usd(r.ppsf)}</span>
            </li>
          )
        })}
      </ul>
      <div className="ex-gfoot">
        {sorted.length > LIMIT && <button className="ghost small" onClick={() => setAll(!all)}>{all ? 'Show fewer' : `Show all ${num(sorted.length)}`}</button>}
        {left > 0 && <span className="muted">{num(left)} {group === 'area' ? nounPlural : 'groups'} with under {MIN_RANKED} sales not ranked.</span>}
        {pick && <span className="muted">Tap a name to filter to it.</span>}
      </div>
    </div>
  )
}

function SalesList({ result, showBeds }: { result: Result; showBeds: boolean; cityId: string }) {
  const ix = Object.fromEntries(result.columns.map((c, i) => [c.name, i]))
  return (
    <div className="ex-sales">
      <div className={`ex-srow ex-srow-head ${showBeds ? '' : 'nobeds'}`} aria-hidden="true">
        <span>Sold</span><span>Address</span>{showBeds && <span className="n">Beds</span>}<span className="n">Sq ft</span><span className="n">$/sq ft</span><span className="n">Price</span>
      </div>
      <ul>
        {result.rows.map((r, i) => (
          <li key={i} className={`ex-srow ${showBeds ? '' : 'nobeds'}`}>
            <span className="ex-sdate">{fmt(r[ix.date], 'date')}</span>
            <span className="ex-saddr"><b>{String(r[ix.address] ?? '–')}</b><em>{[r[ix.area], r[ix.type]].filter(Boolean).join(' · ')}</em></span>
            {showBeds && <span className={`n ex-sbeds ${r[ix.beds] == null ? 'nil' : ''}`}>{r[ix.beds] == null ? '–' : `${r[ix.beds]} bd`}</span>}
            <span className={`n ex-ssize ${r[ix.sqft] == null ? 'nil' : ''}`}>{r[ix.sqft] == null ? '–' : `${num(r[ix.sqft] as number)} sq ft`}</span>
            <span className={`n ex-sppsf ${r[ix.ppsf] == null ? 'nil' : ''}`}>{r[ix.ppsf] == null ? '–' : `${usd(r[ix.ppsf] as number)}/sq ft`}</span>
            <span className="n ex-sprice">{usd(r[ix.price] as number)}</span>
          </li>
        ))}
      </ul>
    </div>
  )
}

// ---------- answers to questions and SQL ----------
function AnswerView({ answer, busy, ask, isSql, noun, back, recentOnly, recentYears, loadFull }: {
  answer: Answer | null; busy: boolean; ask: Ask | null; isSql: boolean; noun: string; back: () => void
  recentOnly: boolean; recentYears: number; loadFull: () => void
}) {
  const [limit, setLimit] = useState(50)
  const [barKey, setBarKey] = useState<string | null>(null)
  useEffect(() => { setLimit(50); setBarKey(null) }, [answer])
  const r = answer?.result
  const cols: (Col & { i: number })[] = r ? (
    ask?.cols
      ? ask.cols.map((c) => ({ ...c, label: c.label || noun, i: r.columns.findIndex((x) => x.name === c.key) })).filter((c) => c.i >= 0)
      : r.columns.map((c, i) => ({ key: c.name, label: c.name, fmt: guessFmt(c.name, c.kind), i }))
  ) : []
  const bars = cols.filter((c) => c.bar)
  const bar = bars.find((c) => c.key === barKey) ?? bars[0]
  const top = bar && r ? Math.max(1e-9, ...r.rows.map((x) => Math.abs(Number(x[bar.i] ?? 0)))) : 1
  return (
    <div className={`ex-answer ${busy ? 'is-busy' : ''}`}>
      <div className="ex-asked">
        <button className="ghost small" onClick={back}>← Back to search</button>
        <div><b>{isSql ? 'Your SQL' : ask?.q}</b> {!isSql && <span className="muted">{ask?.note} Whole city; the filters do not apply here.</span>}</div>
      </div>
      {recentOnly && ask?.longer && <div className="notice ex-more">Only the last {recentYears} years are loaded. <button className="ghost small" onClick={loadFull}>Load full history</button></div>}
      {!answer && <div className="card ex-wait">{busy ? 'Working it out…' : ''}</div>}
      {answer?.stats && ask?.stats && (
        <div className="ex-tiles">
          {ask.stats.cols.map((c) => {
            const i = answer.stats!.columns.findIndex((x) => x.name === c.key)
            return <Tile key={c.key} label={c.label} value={fmt(answer.stats!.rows[0]?.[i], c.fmt)} foot="" />
          })}
        </div>
      )}
      {r && (
        <div className="card">
          <header className="ex-list-head">
            <h3>{num(r.total)} row{r.total === 1 ? '' : 's'}{r.total > r.rows.length ? ` (first ${num(r.rows.length)} kept; CSV has all)` : ''}</h3>
            {bars.length > 1 && (
              <div className="segmented" role="group" aria-label="Bars show">
                {bars.map((b) => <button key={b.key} className={bar?.key === b.key ? 'on' : ''} aria-pressed={bar?.key === b.key} onClick={() => setBarKey(b.key)}>{b.label}</button>)}
              </div>
            )}
          </header>
          {r.total === 0 ? <div className="muted">Nothing matched.</div> : (
            <div className="table-scroll ex-atable">
              <table className="data">
                <thead><tr>
                  {cols.map((c, j) => <th key={j} className={c.fmt === 'text' || c.fmt === 'date' ? '' : 'num'}>{c.label}</th>)}
                </tr></thead>
                <tbody>
                  {r.rows.slice(0, limit).map((row, i) => (
                    <tr key={i}>
                      {cols.map((c, j) => {
                        const v = row[c.i]
                        const isBar = bar && c.key === bar.key
                        const n = Number(v ?? 0)
                        return (
                          <td key={j} className={`${c.fmt === 'text' || c.fmt === 'date' ? '' : 'num'} ${j === 0 ? 'ex-first' : ''}`}>
                            {isBar ? (
                              <span className="ex-cellbar">
                                <span className="ex-gtrack"><span className={`ex-gfill ${n < 0 ? 'neg' : ''}`} style={{ width: `${v == null ? 0 : Math.max(1.5, (Math.abs(n) / top) * 100)}%` }} /></span>
                                {fmt(v, c.fmt)}
                              </span>
                            ) : fmt(v, c.fmt)}
                          </td>
                        )
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {r.rows.length > limit && <div className="ex-gfoot"><button className="ghost small" onClick={() => setLimit(limit + 100)}>Show more</button></div>}
        </div>
      )}
    </div>
  )
}

function SqlBox({ initial, disabled, busy, onRun, onCancel }: { initial: string; disabled: boolean; busy: boolean; onRun: (s: string) => void; onCancel: () => void }) {
  const [text, setText] = useState(initial)
  useEffect(() => setText(initial), [initial])
  return (
    <>
      <textarea className="ex-editor" spellCheck={false} aria-label="SQL query" value={text} rows={Math.min(18, Math.max(6, text.split('\n').length + 1))}
        placeholder="SELECT area, median(price) FROM sales GROUP BY area"
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => { if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') { e.preventDefault(); onRun(text) } }} />
      <div className="ex-actions">
        <button className="ex-run" disabled={disabled || busy || !text.trim()} onClick={() => onRun(text)}>{busy ? 'Running…' : 'Run'}</button>
        {busy && <button className="ghost small" onClick={onCancel}>Cancel</button>}
        <span className="muted">Ctrl/⌘ + Enter runs. DuckDB SQL.</span>
      </div>
    </>
  )
}

const SCHEMA = [
  { table: 'sales', note: 'one row per recorded sale', cols: Object.entries(SALES_TYPES) },
  { table: 'areas', note: 'published 12-month figures', cols: Object.entries(AREA_TYPES) },
]

function Schema({ noun }: { noun: string }) {
  return (
    <aside className="ex-schema" aria-label="Columns">
      {SCHEMA.map((t) => (
        <details key={t.table} open={t.table === 'sales'}>
          <summary><b>{t.table}</b> <span className="muted">{t.note}</span></summary>
          <dl>
            {t.cols.map(([c, ty]) => <div key={c}><dt>{c}</dt><dd>{ty}</dd></div>)}
          </dl>
        </details>
      ))}
      <p className="muted">area is the {noun.toLowerCase()}; join with <code>USING (area)</code>. Unknown values are NULL.</p>
    </aside>
  )
}
