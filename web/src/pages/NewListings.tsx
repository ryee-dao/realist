import '../styles/finder.css'
import '../styles/listing-cards.css'
import { useEffect, useMemo, useRef, useState } from 'react'
import { Area, Areas, City, useData } from '../lib/data'
import { Theme } from '../lib/hooks'
import { usdCompact } from '../lib/format'
import { activityPercentiles, percentiles, rankPool, Tier, tierStyle, useRamp } from '../lib/grades'
import ListingsMap, { ListingPin } from '../components/ListingsMap'

// ---- engine/listing_cards.py output ----
type Item = { text: string; kind: 'model' | 'area' | 'sold' | 'description' | 'photo'; why?: string }
type Part = { label: string; pct: number; driver: string; notRecorded?: boolean }
type Estimate = {
  kind: 'model' | 'ppsf' | 'median'; value: number; vsPct: number; low?: number; high?: number
  error: number | null; errorBand?: string | null
  base?: number; area?: { label: string; pct: number }; parts?: Part[]
  ppsf?: number; sqft?: number
}
type Card = {
  id: string; url: string; address: string | null; date: string | null; price: number | null; source: string | null
  links: { source: string | null; url: string }[]
  facts: { beds?: number; baths?: number; sqft?: number; type?: string; homeType?: string }
  lat?: number; lon?: number; inSnapshot?: false; firstSeen?: string; lastSeen?: string
  area?: { key: string; name: string; mapping: string }
  sold?: { ppsf12?: number; median12?: number; sales12?: number; vsPpsfPct?: number; vsMedianPct?: number; topType?: string; topTypeShare?: number }
  estimate?: Estimate
  lastSale?: { date: string; price: number; recentResale?: boolean }
  plus: Item[]; minus: Item[]; tags?: string[]
  photo?: { findings: number; crops: { src: string; label: string }[]; credit: string | null; creditUrl: string | null
    condition: string | null; coverage: string | null; seen: string | null; possiblyEdited: boolean }
}
type CardsDoc = {
  city: string; asOf: string | null; from: string | null; through: string | null
  sold: { through: string | null; basis: string }
  model: { error: number | null; ppsfError: number | null; level: string[] | null } | null
  photoTagging: boolean; retentionDays: number | null
  summary: { cards: number; withPin: number; withSqft: number }
  cards: Card[]
}

// ---- area tiers: the Find areas factors and maths (lib/grades.ts), with this page's own saved weights ----
type Factor = 'yield' | 'growth1' | 'growth5' | 'price' | 'activity' | 'crime'
type Weights = Record<Factor, number>
const FACTORS: { id: Factor; label: string }[] = [
  { id: 'yield', label: 'Rental yield' }, { id: 'growth1', label: 'Growth, 1 yr' }, { id: 'growth5', label: 'Growth, 5 yr' },
  { id: 'price', label: 'Lower price' }, { id: 'activity', label: 'Active market' }, { id: 'crime', label: 'Fewer incidents' },
]
const IDS = FACTORS.map((f) => f.id)
const PRESETS: { id: string; label: string; w: Weights }[] = [
  { id: 'rental', label: 'Rental income', w: { yield: 5, growth1: 1, growth5: 1, price: 1, activity: 2, crime: 1 } },
  { id: 'growth', label: 'Price growth', w: { yield: 0, growth1: 4, growth5: 4, price: 0, activity: 2, crime: 1 } },
  { id: 'entry', label: 'Low entry price', w: { yield: 2, growth1: 1, growth5: 0, price: 5, activity: 2, crime: 1 } },
  { id: 'balanced', label: 'Balanced', w: { yield: 2, growth1: 2, growth5: 2, price: 2, activity: 2, crime: 2 } },
]
const PAGE = 150       // cards (and their pins) shown before "Show more"
const STALE_DAYS = 14   // a home no publisher's read has listed for this long may have sold
const MIN_SALES = 30   // the Find areas default pool
function raw(a: Area, f: Factor): number | null {
  switch (f) {
    case 'yield': return a.yield
    case 'growth1': return a.priceGrowth1 ?? a.medianYoY
    case 'growth5': return a.priceGrowth5 ?? a.change5y
    case 'price': return a.median12 == null ? null : -a.median12
    case 'activity': return a.sales12
    case 'crime': return a.violentRate == null || a.propertyRate == null ? null : -(a.violentRate * 3 + a.propertyRate)
  }
}
const presetFor = (w: Weights) => PRESETS.find((p) => IDS.every((id) => p.w[id] === w[id]))

// ---- browser storage (never required: a private window just forgets) ----
const W_KEY = 'realist-listing-weights'
const HIDE_KEY = (city: string) => `realist-hidden-listings:${city}`
function readJSON<T>(key: string, fallback: T): T {
  try { const v = localStorage.getItem(key); return v ? JSON.parse(v) as T : fallback } catch { return fallback }
}
function writeJSON(key: string, value: unknown) {
  try { localStorage.setItem(key, JSON.stringify(value)) } catch { /* storage unavailable */ }
}
function savedWeights(): Weights {
  const w = readJSON<Partial<Weights> | null>(W_KEY, null)
  return w && IDS.every((id) => Number.isInteger(w[id]) && w[id]! >= 0 && w[id]! <= 5) && IDS.some((id) => w[id]! > 0)
    ? w as Weights : PRESETS[0].w
}

/** The page is not handed the theme, so follow the attribute the app sets on <html>. */
function useDocTheme(): Theme {
  const get = (): Theme => (document.documentElement.dataset.theme === 'light' ? 'light' : 'dark')
  const [theme, setTheme] = useState<Theme>(get)
  useEffect(() => {
    const mo = new MutationObserver(() => setTheme(get()))
    mo.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] })
    return () => mo.disconnect()
  }, [])
  return theme
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
const shortDate = (d: string) => `${MONTHS[+d.slice(5, 7) - 1]} ${+d.slice(8, 10)}`
const monthYear = (d: string) => `${MONTHS[+d.slice(5, 7) - 1]} ${d.slice(0, 4)}`
const money = (n: number) => '$' + Math.round(n).toLocaleString('en-US')
const ago = (days: number) => days <= 0 ? 'today' : days === 1 ? 'yesterday' : days < 60 ? `${days} days ago` : `${Math.round(days / 30)} months ago`
const pinLabel = (n: number) => (n >= 995_000 ? `$${(n / 1e6).toFixed(n >= 9_950_000 ? 0 : 1).replace(/[.]0$/, '')}M` : `$${Math.max(1, Math.round(n / 1e3))}K`)
const ageClass = (days: number): 0 | 1 | 2 => (days < 30 ? 0 : days < 90 ? 1 : 2)

export default function NewListings({ city }: { city: City }) {
  const { data, error } = useData<CardsDoc>(`${city.id}/listing-cards.json`)
  const { data: areasDoc } = useData<Areas>(`${city.id}/areas.json`)
  const theme = useDocTheme()
  const ramp = useRamp(theme)
  const [w, setW] = useState<Weights>(savedWeights)
  const [hidden, setHidden] = useState<string[]>(() => readJSON<string[]>(HIDE_KEY(city.id), []))
  const [showHidden, setShowHidden] = useState(false)
  const [hover, setHover] = useState<string | null>(null)
  const [picked, setPicked] = useState<string | null>(null)
  const [view, setView] = useState<'list' | 'map'>('list')
  const [limit, setLimit] = useState(PAGE)   // homes accumulate for months; cards and pins are shown a page at a time
  const listRef = useRef<HTMLDivElement>(null)

  useEffect(() => writeJSON(W_KEY, w), [w])
  useEffect(() => writeJSON(HIDE_KEY(city.id), hidden), [hidden, city.id])

  const timeZone = city.id === 'chicago' ? 'America/Chicago' : 'America/New_York'
  const today = new Intl.DateTimeFormat('sv-SE', { timeZone }).format(new Date())
  const daysSince = (d: string | null) => (d ? Math.round((Date.parse(today) - Date.parse(d)) / 86400000) : 0)

  // area tiers under the current weights (same pool, factors and ranking as Find areas)
  const tiers = useMemo(() => {
    const list = (areasDoc?.areas ?? []).filter((a) => a.median12 != null && a.sales12 >= MIN_SALES)
    const pc = FACTORS.map((f) => f.id === 'activity'
      ? activityPercentiles(list, (a) => a.key, (a) => a.sales12, [(a) => a.listings?.active ?? null, (a) => a.listings?.newListings ?? null])
      : percentiles(list, (a) => a.key, (a) => raw(a, f.id)))
    const pool = list.map((a) => ({ key: a.key, pctl: Object.fromEntries(IDS.flatMap((id, i) => (pc[i].has(a.key) ? [[id, pc[i].get(a.key)]] : []))) as Partial<Record<Factor, number>> }))
    return new Map(rankPool(pool, IDS, w).ranked.map((r) => [r.key, { tier: r.tier, rank: r.rank, of: pool.length }]))
  }, [areasDoc, w])

  const hiddenSet = useMemo(() => new Set(hidden), [hidden])
  const visible = useMemo(() => (data?.cards ?? []).filter((c) => showHidden || !hiddenSet.has(c.id)), [data, hiddenSet, showHidden])
  const cards = useMemo(() => visible.slice(0, limit), [visible, limit])
  const hiddenCount = (data?.cards ?? []).filter((c) => hiddenSet.has(c.id)).length
  const pins: ListingPin[] = useMemo(() => cards.filter((c) => c.lat != null && c.lon != null).map((c) => ({
    id: c.id, lat: c.lat!, lon: c.lon!, label: c.price ? pinLabel(c.price) : '?', age: ageClass(daysSince(c.date)),
    tip: `Asking ${c.price ? money(c.price) : 'not given'} · ${c.address ?? ''}`,
  // eslint-disable-next-line react-hooks/exhaustive-deps
  })), [cards, today])

  const pick = (id: string) => {
    setPicked(id)
    requestAnimationFrame(() => listRef.current?.querySelector(`[data-id="${CSS.escape(id)}"]`)?.scrollIntoView({ block: 'nearest', behavior: 'smooth' }))
  }
  const toggleHide = (id: string) => setHidden((h) => (h.includes(id) ? h.filter((x) => x !== id) : [...h, id]))
  const active = hover ?? picked

  if (!data) return <section className="card"><p className="muted">{error ? 'Listings could not be loaded.' : 'Loading listings…'}</p></section>

  const noPin = cards.length - pins.length
  const pickedCard = cards.find((c) => c.id === picked)
  const cardProps = { city, data, tiers, ramp, daysSince, hiddenSet, toggleHide }

  return <div className="lc">
    <section className="card lc-head">
      <p className="lc-lede">
        {data.summary.cards} homes for sale{data.from && data.through ? `, listed ${shortDate(data.from)}–${shortDate(data.through)}` : ''}, newest first.
        {' '}Asking prices from publishers’ pages{data.asOf ? `, read ${new Date(data.asOf).toLocaleDateString('en-US', { month: 'short', day: 'numeric', timeZone })}` : ''}.
      </p>
      <Weights w={w} setW={setW} tiers={tiers.size} />
      <div className="lc-bar">
        <div className="segmented lc-toggle" role="group" aria-label="View">
          {(['list', 'map'] as const).map((v) => <button key={v} className={view === v ? 'on' : ''} aria-pressed={view === v} onClick={() => setView(v)}>{v === 'list' ? 'List' : 'Map'}</button>)}
        </div>
        {hiddenCount > 0 && <span className="muted">Hidden ({hiddenCount}) · <button className="lc-link" onClick={() => setShowHidden(!showHidden)}>{showHidden ? 'hide again' : 'show'}</button></span>}
        {noPin > 0 && <span className="muted">{noPin} without a map location</span>}
      </div>
    </section>

    <div className={`lc-split view-${view}`}>
      <div className="lc-mapcol">
        <ListingsMap center={city.center} zoom={city.zoom} pins={pins} active={active} focus={picked} onHover={setHover} onPick={pick}
          height="var(--lc-map-h)" visible={view === 'map'} />
        <div className="map-legend lc-legend" aria-label="Pin style by days on market">
          <span><i className="lc-pin a0">$</i>under 30 days</span><span><i className="lc-pin a1">$</i>30–90 days</span><span><i className="lc-pin a2">$</i>over 90 days</span>
          <span className="muted">Pins show asking price</span>
        </div>
        {view === 'map' && pickedCard && <div className="lc-mapcard"><ListingCard c={pickedCard} on {...cardProps} onHover={() => {}} /></div>}
      </div>
      <div className="lc-list" ref={listRef}>
        {cards.map((c) => <ListingCard key={c.id} c={c} on={c.id === active} onHover={setHover} {...cardProps} />)}
        {!cards.length && <p className="muted">No homes to show.</p>}
        {visible.length > cards.length && <button className="ghost" onClick={() => setLimit(limit + PAGE)}>
          Show more ({visible.length - cards.length} older)</button>}
      </div>
    </div>

    <p className="footnote">{data.photoTagging ? 'Photo notes come from an AI model and can be wrong.' : 'Photo analysis: Philadelphia only for now.'}</p>
  </div>
}

// ---- weights control ----
function Weights({ w, setW, tiers }: { w: Weights; setW: (w: Weights) => void; tiers: number }) {
  const preset = presetFor(w)
  return <details className="lc-weights">
    <summary>Area tiers: <b>{preset?.label ?? 'Custom weights'}</b> <span className="muted">· change</span></summary>
    <div className="lc-weights-body">
      <div className="segmented" role="group" aria-label="Weights">
        {PRESETS.map((p) => <button key={p.id} className={preset?.id === p.id ? 'on' : ''} aria-pressed={preset?.id === p.id} onClick={() => setW(p.w)}>{p.label}</button>)}
      </div>
      <div className="weights f-weights">
        {FACTORS.map((f) => <div className="weight" key={f.id}>
          <label className="weight-top" htmlFor={`lw-${f.id}`}><b>{f.label}</b><span>{w[f.id] === 0 ? 'off' : w[f.id]}</span></label>
          <input id={`lw-${f.id}`} type="range" min={0} max={5} step={1} value={w[f.id]}
            onChange={(e) => { const nw = { ...w, [f.id]: +e.target.value }; if (IDS.some((id) => nw[id] > 0)) setW(nw) }} />
        </div>)}
      </div>
      <p className="muted lc-small">Same scoring as Find areas: {tiers} areas with {MIN_SALES}+ sales a year, S best to D. Saved in this browser.</p>
    </div>
  </details>
}

// ---- one card ----
interface CardProps {
  c: Card; on: boolean; onHover: (id: string | null) => void
  city: City; data: CardsDoc; tiers: Map<string, { tier: Tier; rank: number; of: number }>
  ramp: ReturnType<typeof useRamp>; daysSince: (d: string | null) => number
  hiddenSet: Set<string>; toggleHide: (id: string) => void
}

function ListingCard({ c, on, onHover, city, data, tiers, ramp, daysSince, hiddenSet, toggleHide }: CardProps) {
  const days = daysSince(c.date)
  const t = c.area ? tiers.get(c.area.key) : undefined
  const f = c.facts
  const facts = [f.beds != null && `${f.beds} bd`, f.baths != null && `${f.baths} ba`, f.sqft && `${f.sqft.toLocaleString('en-US')} sq ft`,
    f.sqft && c.price && `$${Math.round(c.price / f.sqft)}/sq ft`, f.type ?? f.homeType].filter(Boolean).join(' · ')
  const isHidden = hiddenSet.has(c.id)
  return <article className={`lc-card ${on ? 'on' : ''} ${isHidden ? 'is-hidden' : ''}`} data-id={c.id}
    onMouseEnter={() => onHover(c.id)} onMouseLeave={() => onHover(null)}>
    <div className="lc-top">
      <div>
        <div className="lc-price"><small>Asking</small> {c.price ? money(c.price) : 'not given'}</div>
        <div className="muted lc-small">{c.date ? <>Listed {shortDate(c.date)} · {ago(days)}</> : 'List date not given'}{c.inSnapshot === false ? ' · not in the latest read' : ''}</div>
        {c.lastSeen && daysSince(c.lastSeen) > STALE_DAYS && <div className="muted lc-small">Last seen {shortDate(c.lastSeen)} — may be off the market</div>}
      </div>
      {c.estimate && c.price ? <Headline c={c} e={c.estimate} city={city} data={data} /> : null}
    </div>
    <a className="lc-addr" href={c.url} target="_blank" rel="noopener noreferrer">{c.address ?? 'Address not given'}</a>
    {facts && <div className="muted lc-small">{facts}</div>}
    {c.area && <div className="lc-area">
      {t ? <span className="f-tier" style={tierStyle(t.tier, ramp)} aria-hidden="true">{t.tier}</span> : null}
      <span>{t ? `Area tier ${t.tier}` : 'No tier'} · {c.area.name}</span>
      <a className="lc-small" href={`#/${city.id}/areas?area=${encodeURIComponent(c.area.key)}`}>Detailed View ›</a>
    </div>}
    <PlusMinus c={c} />
    {c.tags?.length || c.lastSale ? <div className="lc-tags">
      {c.lastSale && <span className="chip" title="The latest recorded sale of this address (public records)">Last sold {usdCompact(c.lastSale.price)}, {monthYear(c.lastSale.date)}</span>}
      {c.tags?.map((x) => <span key={x} className="chip">{x}</span>)}
    </div> : null}
    {data.photoTagging && <Photos c={c} />}
    <div className="lc-foot">
      <span className="lc-small">{c.links.map((l, i) => <span key={l.url}>{i ? ' · ' : ''}<a href={l.url} target="_blank" rel="noopener noreferrer">{l.source ?? 'Listing'} ↗</a></span>)}</span>
      <button className="lc-link" onClick={() => toggleHide(c.id)}>{isHidden ? 'Unhide' : 'Hide'}</button>
    </div>
  </article>
}

function PlusMinus({ c }: { c: Card }) {
  const [all, setAll] = useState(false)
  const MAX = 4
  // the home itself first (photos, description, price model), then the price against sales, then the area
  const order = (k: Item['kind']) => ['photo', 'description', 'model', 'sold', 'area'].indexOf(k)
  const byKind = (a: Item, b: Item) => order(a.kind) - order(b.kind)
  const rows = [...[...c.plus].sort(byKind).map((x) => ({ ...x, s: '+' as const })), ...[...c.minus].sort(byKind).map((x) => ({ ...x, s: '−' as const }))]
  if (!rows.length) return null
  const shown = all ? rows : [...rows.filter((r) => r.s === '+').slice(0, MAX), ...rows.filter((r) => r.s === '−').slice(0, MAX)]
  return <ul className="lc-pm">
    {shown.map((r, i) => <li key={i} className={r.s === '+' ? 'plus' : 'minus'} title={r.why}>
      <b aria-label={r.s === '+' ? 'plus' : 'minus'}>{r.s}</b><span>{r.text}</span></li>)}
    {shown.length < rows.length && <li><button className="lc-link" onClick={() => setAll(true)}>+{rows.length - shown.length} more</button></li>}
  </ul>
}

function Photos({ c }: { c: Card }) {
  const p = c.photo
  if (!p) return <div className="muted lc-small">Photos not analysed</div>
  const href = p.creditUrl ?? c.url
  const credit = p.credit ?? c.source ?? 'the publisher'
  return <div className="lc-photos">
    <span className="lc-small">{p.findings ? `${p.findings} ${p.findings === 1 ? 'defect' : 'defects'} noted in photos` : 'No defects noted in photos (photos can’t rule problems out)'}</span>
    {p.crops.length > 0 && <span className="lc-crops">
      {p.crops.slice(0, 5).map((x) => <a key={x.src} href={href} target="_blank" rel="noopener noreferrer" title={`${x.label}. Crop of a ${credit} photo; opens the listing.`}>
        <img src={`data/${x.src}`} alt={`${x.label}, detected in a listing photo`} loading="lazy" width={36} height={36} /></a>)}
      {p.crops.length > 5 && <a className="lc-more" href={href} target="_blank" rel="noopener noreferrer" title={`More crops of ${credit} photos; opens the listing.`}>+{p.crops.length - 5}</a>}
    </span>}
  </div>
}

// ---- the headline number and its breakdown ----
function Headline({ c, e, city, data }: { c: Card; e: Estimate; city: City; data: CardsDoc }) {
  const [open, setOpen] = useState(false)
  const [pinned, setPinned] = useState(false)
  const box = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!pinned) return
    const off = (ev: MouseEvent | TouchEvent) => { if (!box.current?.contains(ev.target as Node)) { setPinned(false); setOpen(false) } }
    document.addEventListener('mousedown', off)
    document.addEventListener('touchstart', off)
    return () => { document.removeEventListener('mousedown', off); document.removeEventListener('touchstart', off) }
  }, [pinned])
  const v = Math.round(e.vsPct)
  const within = e.error != null && Math.abs(e.vsPct) <= e.error
  const what = e.kind === 'model' ? 'estimate' : e.kind === 'ppsf' ? 'sold $/sq ft' : 'area median'
  const word = v === 0 ? 'at' : v < 0 ? 'under' : 'over'
  return <div className="lc-head-num" ref={box} onMouseEnter={() => setOpen(true)} onMouseLeave={() => !pinned && setOpen(false)}>
    <button className={`lc-score ${within ? 'within' : v < 0 ? 'under' : 'over'}`} aria-expanded={open}
      onClick={() => { setPinned(!pinned); setOpen(!pinned) }} onFocus={() => setOpen(true)} onBlur={() => !pinned && setOpen(false)}>
      <b>{v === 0 ? '≈0%' : `${Math.abs(v)}% ${word}`}</b>
      <small>{what}{e.kind !== 'median' ? ` ~${usdCompact(e.value)}` : ''}{within ? ' · within typical error' : ''}</small>
    </button>
    {open && <Breakdown c={c} e={e} city={city} data={data} />}
  </div>
}

function Bar({ pct, max }: { pct: number; max: number }) {
  const w = Math.min(50, (Math.abs(pct) / max) * 50)
  return <span className="lc-bar-track" aria-hidden="true"><i className={pct >= 0 ? 'up' : 'down'} style={pct >= 0 ? { left: '50%', width: `${w}%` } : { right: '50%', width: `${w}%` }} /></span>
}

function Breakdown({ c, e, city, data }: { c: Card; e: Estimate; city: City; data: CardsDoc }) {
  const through = data.sold.through ? monthYear(`${data.sold.through}-01`) : null
  const range = e.low && e.high ? `${usdCompact(e.low)}–${usdCompact(e.high)}` : null
  const err = e.error != null ? <p className="lc-err">Typical error ±{Math.round(e.error)}%{e.errorBand ? ` for ${e.errorBand} homes` : ''}: half of past sales landed further from {e.kind === 'model' ? 'the model' : 'this rule'} than that.</p> : null
  const drivers = city.id === 'philadelphia' ? <a href={`#/${city.id}/drivers`}>Price drivers ›</a> : null
  if (e.kind === 'model') {
    const rows = [e.area!, ...(e.parts ?? [])]
    const max = Math.max(20, ...rows.map((r) => Math.abs(r.pct)))
    return <div className="lc-pop" role="dialog" aria-label="How the estimate is built">
      <p className="lc-pop-title">Estimate about {usdCompact(e.value)}{range ? <span className="muted"> · range {range}</span> : null}</p>
      <div className="lc-rows">
        <div className="lc-row base"><span>City reference home</span><span className="lc-v">{usdCompact(e.base)}</span></div>
        {rows.map((r) => <div className={`lc-row ${(r as Part).notRecorded ? 'nr' : ''}`} key={r.label}>
          <span>{r.label}</span><Bar pct={r.pct} max={max} /><span className="lc-v">{r.pct > 0 ? '+' : '−'}{Math.abs(Math.round(r.pct))}%</span></div>)}
      </div>
      {err}
      <p className="muted lc-small">Priced from the city’s property record for this address{c.lastSale?.recentResale ? `; it last sold for ${usdCompact(c.lastSale.price)} in ${monthYear(c.lastSale.date)}, so work since may not be in the record` : ', which can be out of date'}. Not an appraisal. {drivers}</p>
    </div>
  }
  if (e.kind === 'ppsf') {
    return <div className="lc-pop" role="dialog" aria-label="How the comparison is built">
      <p className="lc-pop-title">About {usdCompact(e.value)} at the area’s sold $/sq ft</p>
      <div className="lc-rows">
        <div className="lc-row base"><span>Median sold $/sq ft, {c.area?.name}</span><span className="lc-v">${e.ppsf}</span></div>
        <div className="lc-row base"><span>× floor area</span><span className="lc-v">{e.sqft?.toLocaleString('en-US')} sq ft</span></div>
        <div className="lc-row base"><span>Asking $/sq ft</span><span className="lc-v">${c.price && e.sqft ? Math.round(c.price / e.sqft) : '–'}</span></div>
      </div>
      {err ?? <p className="lc-err">Not tested in this city. In Philadelphia this rule was typically off by ±{Math.round(data.model?.ppsfError ?? 25)}%.</p>}
      <p className="muted lc-small">{c.sold?.sales12 ?? 'Recorded'} sales in 12 complete months{through ? ` to ${through}` : ''}. Size only: ignores condition, lot and exact spot. {drivers}</p>
    </div>
  }
  return <div className="lc-pop" role="dialog" aria-label="How the comparison is built">
    <p className="lc-pop-title">Area median sold price {usdCompact(e.value)}</p>
    <p className="lc-err">Ignores size and type{c.sold?.topType ? `; sales here are ${c.sold.topTypeShare ?? ''}% ${c.sold.topType.toLowerCase()}` : ''}. A rough guide only.</p>
    <p className="muted lc-small">{c.sold?.sales12 ?? 'Recorded'} sales in 12 complete months{through ? ` to ${through}` : ''}.{c.facts.sqft ? '' : ' No floor area given, so no $/sq ft comparison.'}</p>
  </div>
}
