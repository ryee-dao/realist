import '../styles/finder.css'
import '../styles/listing-cards.css'
import { useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { Area, Areas, City, useData } from '../lib/data'
import { Theme } from '../lib/hooks'
import { usdCompact } from '../lib/format'
import { activityPercentiles, percentiles, rankPool, Tier, TIERS, tierStyle, useRamp } from '../lib/grades'
import ListingsMap, { ListingPin } from '../components/ListingsMap'
import ListingPanel from '../components/ListingPanel'
import { Breakdown, Card, CardsDoc, CropImage, Item, LOW_MSG, money, monthYear, shortDate, WarnMark } from '../components/listingParts'
import { safeUrl } from '../lib/safeUrl'

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
const PER_PAGE = 10
const STALE_DAYS = 14   // a home no publisher's read has listed for this long may have sold
const MIN_SALES = 30    // the Find areas default pool
const SHOWN_ITEMS = 3   // plus or minus lines per card before "+N"
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

// ---- filters and sort, kept in the hash query (#/city/listings?min=…&sort=…) ----
type Filters = { min: string; max: string; beds: string; baths: string; tier: string; cond: string; est: string; th: string; sort: string; page: string; home: string }
const DEFAULTS: Filters = { min: '', max: '', beds: '', baths: '', tier: '', cond: '', est: '', th: '10', sort: 'new', page: '1', home: '' }
const SORTS = [['new', 'Newest'], ['old', 'Oldest'], ['plo', 'Price: low to high'], ['phi', 'Price: high to low'], ['gap', 'Most under estimate']]
function readFilters(): Filters {
  const q = new URLSearchParams(window.location.hash.split('?')[1] ?? '')
  return Object.fromEntries(Object.entries(DEFAULTS).map(([k, v]) => [k, q.get(k) ?? v])) as Filters
}
function hashFor(f: Filters) {
  const q = new URLSearchParams(Object.entries(f).filter(([k, v]) => v !== DEFAULTS[k as keyof Filters]))
  return `${window.location.hash.split('?')[0]}${q.toString() ? `?${q}` : ''}`
}
function writeFilters(f: Filters) {
  try {
    if (hashFor(f) !== window.location.hash) history.replaceState(null, '', hashFor(f))
  } catch { /* not allowed: state just is not shared */ }
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

const ago = (days: number) => days <= 0 ? 'today' : days === 1 ? 'yesterday' : days < 60 ? `${days} days ago` : `${Math.round(days / 30)} months ago`
const pinLabel = (n: number) => (n >= 995_000 ? `$${(n / 1e6).toFixed(n >= 9_950_000 ? 0 : 1).replace(/[.]0$/, '')}M` : `$${Math.max(1, Math.round(n / 1e3))}K`)
const ageClass = (days: number): 0 | 1 | 2 => (days < 30 ? 0 : days < 90 ? 1 : 2)
const hasSignal = (c: Card, ...names: string[]) => names.some((n) => c.signals?.includes(n))

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
  const [f, setF] = useState<Filters>(readFilters)
  const [drawer, setDrawer] = useState(false)
  const listRef = useRef<HTMLDivElement>(null)
  const [title, setTitle] = useState<Element | null>(null)

  useEffect(() => writeJSON(W_KEY, w), [w])
  useEffect(() => writeJSON(HIDE_KEY(city.id), hidden), [hidden, city.id])
  useEffect(() => writeFilters(f), [f])
  useEffect(() => setTitle(document.querySelector('.page-head h1')), [])
  const set = (patch: Partial<Filters>) => setF((x) => ({ ...x, page: '1', ...patch }))

  const timeZone = city.id === 'chicago' ? 'America/Chicago' : 'America/New_York'
  const today = new Intl.DateTimeFormat('sv-SE', { timeZone }).format(new Date())
  const daysSince = (d: string | null) => (d ? Math.round((Date.parse(today) - Date.parse(d)) / 86400000) : 0)

  // area tiers under the current weights (same pool, factors and ranking as Find areas)
  const tiers = useMemo(() => {
    const list = (areasDoc?.areas ?? []).filter((a) => a.median12 != null && a.sales12 >= MIN_SALES)
    const pc = FACTORS.map((x) => x.id === 'activity'
      ? activityPercentiles(list, (a) => a.key, (a) => a.sales12, [(a) => a.listings?.active ?? null, (a) => a.listings?.newListings ?? null])
      : percentiles(list, (a) => a.key, (a) => raw(a, x.id)))
    const pool = list.map((a) => ({ key: a.key, pctl: Object.fromEntries(IDS.flatMap((id, i) => (pc[i].has(a.key) ? [[id, pc[i].get(a.key)]] : []))) as Partial<Record<Factor, number>> }))
    return new Map(rankPool(pool, IDS, w).ranked.map((r) => [r.key, r.tier]))
  }, [areasDoc, w])

  const hiddenSet = useMemo(() => new Set(hidden), [hidden])
  const hiddenCount = (data?.cards ?? []).filter((c) => hiddenSet.has(c.id)).length
  // the filter fields only: paging and opening a home must not re-filter (and so re-pin the map)
  const { min: fMin, max: fMax, beds: fBeds, baths: fBaths, tier: fTier, cond: fCond, est: fEst, th: fTh, sort: fSort } = f
  const filtered = useMemo(() => {
    const f = { min: fMin, max: fMax, beds: fBeds, baths: fBaths, tier: fTier, cond: fCond, est: fEst, th: fTh, sort: fSort }
    const min = +f.min || 0, max = +f.max || Infinity, th = +f.th || 10
    const tierMax = f.tier ? TIERS.indexOf(f.tier as Tier) : 4
    const out = (data?.cards ?? []).filter((c) => {
      if (!showHidden && hiddenSet.has(c.id)) return false
      if ((f.min || f.max) && (c.price == null || c.price < min || c.price > max)) return false
      if (f.beds && !((c.facts.beds ?? -1) >= +f.beds)) return false
      if (f.baths && !((c.facts.baths ?? -1) >= +f.baths)) return false
      if (f.tier) { const t = c.area && tiers.get(c.area.key); if (!t || TIERS.indexOf(t) > tierMax) return false }
      if (f.cond === 'defects' && !c.photo?.findings) return false
      if (f.cond === 'asis' && !hasSignal(c, 'as_is', 'needs_work', 'investor_special')) return false
      if (f.cond === 'renovated' && !hasSignal(c, 'renovated', 'new_construction')) return false
      if (f.cond === 'none' && c.photo) return false
      if (f.est === 'under' && !(c.estimate && c.estimate.vsPct <= -th)) return false
      if (f.est === 'over' && !(c.estimate && c.estimate.vsPct >= th)) return false
      return true
    })
    const by = {
      new: (a: Card, b: Card) => (b.date ?? '').localeCompare(a.date ?? ''),
      old: (a: Card, b: Card) => (a.date ?? '').localeCompare(b.date ?? ''),
      plo: (a: Card, b: Card) => (a.price ?? Infinity) - (b.price ?? Infinity),
      phi: (a: Card, b: Card) => (b.price ?? -1) - (a.price ?? -1),
      gap: (a: Card, b: Card) => (a.estimate?.vsPct ?? Infinity) - (b.estimate?.vsPct ?? Infinity),
    }[f.sort] ?? (() => 0)
    return out.sort(by)
  }, [data, fMin, fMax, fBeds, fBaths, fTier, fCond, fEst, fTh, fSort, tiers, hiddenSet, showHidden])

  const pages = Math.max(1, Math.ceil(filtered.length / PER_PAGE))
  const page = Math.min(pages, Math.max(1, +f.page || 1))
  const cards = useMemo(() => filtered.slice((page - 1) * PER_PAGE, page * PER_PAGE), [filtered, page])
  const onPage = useMemo(() => new Set(cards.map((c) => c.id)), [cards])
  const pins: ListingPin[] = useMemo(() => {
    const all = filtered.filter((c) => c.lat != null && c.lon != null).map((c) => ({
      id: c.id, lat: c.lat!, lon: c.lon!, label: c.price ? pinLabel(c.price) : '?', age: ageClass(daysSince(c.date)),
      tip: `Asking ${c.price ? money(c.price) : 'not given'} · ${c.address ?? ''}`, dim: !onPage.has(c.id),
    }))
    return [...all.filter((p) => !p.dim), ...all.filter((p) => p.dim)]   // the page's pins are labelled first
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filtered, onPage, today])

  const goPage = (p: number) => {
    setF((x) => ({ ...x, page: String(p) }))
    requestAnimationFrame(() => listRef.current?.scrollIntoView({ block: 'start', behavior: 'smooth' }))
  }
  const pick = (id: string) => {
    const i = filtered.findIndex((c) => c.id === id)
    if (i >= 0 && Math.floor(i / PER_PAGE) + 1 !== page) setF((x) => ({ ...x, page: String(Math.floor(i / PER_PAGE) + 1) }))
    setPicked(id)
    setTimeout(() => listRef.current?.querySelector(`[data-id="${CSS.escape(id)}"]`)?.scrollIntoView({ block: 'nearest', behavior: 'smooth' }), 50)
  }
  const toggleHide = (id: string) => setHidden((h) => (h.includes(id) ? h.filter((x) => x !== id) : [...h, id]))
  const active = hover ?? picked

  // the detail panel: ?home=<id> in the hash, pushed so Back closes it
  const pushed = useRef(false)
  const openHome = (id: string) => {
    if (!f.home) {
      try { history.pushState(null, '', hashFor({ ...readFilters(), home: id })); pushed.current = true } catch { /* no history: Close still works */ }
    }
    setF((x) => ({ ...x, home: id }))
    setPicked(id)
  }
  const closeHome = () => {
    if (pushed.current) { pushed.current = false; history.back() } else setF((x) => ({ ...x, home: '' }))
  }
  useEffect(() => {
    // Back/Forward: re-read the hash; keep the scroll position the app resets on a hash change
    const pop = () => { const y = window.scrollY; pushed.current = false; setF(readFilters()); requestAnimationFrame(() => window.scrollTo(0, y)) }
    window.addEventListener('popstate', pop)
    return () => window.removeEventListener('popstate', pop)
  }, [])
  const openCard = (data?.cards ?? []).find((c) => c.id === f.home)

  const badge = title && data ? createPortal(<span className="lc-count" aria-label={`${filtered.length} homes`}>{filtered.length}</span>, title) : null
  if (!data) return <section className="card"><p className="muted">{error ? 'Listings could not be loaded.' : 'Loading listings…'}</p></section>

  const cardProps = { city, tiers, ramp, daysSince, hiddenSet, toggleHide, photoCity: data.photoTagging, onOpen: openHome }
  const activeCount = (['min', 'max', 'beds', 'baths', 'tier', 'cond', 'est'] as const).filter((k) => f[k]).length

  return <div className="lc">
    {badge}
    <section className="card lc-controls">
      <div className="lc-bar">
        <button className={`ghost small lc-drawer-btn ${drawer ? 'on' : ''}`} aria-expanded={drawer} onClick={() => setDrawer(!drawer)}>
          Filters{activeCount ? ` (${activeCount})` : ''}</button>
        <div className="segmented lc-toggle" role="group" aria-label="View">
          {(['list', 'map'] as const).map((v) => <button key={v} className={view === v ? 'on' : ''} aria-pressed={view === v} onClick={() => setView(v)}>{v === 'list' ? 'List' : 'Map'}</button>)}
        </div>
        {(activeCount > 0 || hiddenCount > 0) && <span className="lc-small muted lc-clear">
          {activeCount > 0 && <button className="lc-link" onClick={() => setF({ ...DEFAULTS, sort: f.sort })}>Clear filters</button>}
          {activeCount > 0 && hiddenCount > 0 && ' · '}
          {hiddenCount > 0 && <>Hidden ({hiddenCount}) · <button className="lc-link" onClick={() => setShowHidden(!showHidden)}>{showHidden ? 'hide' : 'show'}</button></>}
        </span>}
        <label className="lc-f lc-sort"><span>Sort</span>
          <select value={f.sort} onChange={(e) => set({ sort: e.target.value })}>{SORTS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></label>
      </div>
      <div className={`lc-filters ${drawer ? 'open' : ''}`}>
        <label className="lc-f"><span>Price</span>
          <span className="lc-range">
            <input inputMode="numeric" placeholder="Min" value={f.min} aria-label="Minimum asking price" onChange={(e) => set({ min: e.target.value.replace(/\D/g, '') })} />
            <span>–</span>
            <input inputMode="numeric" placeholder="Max" value={f.max} aria-label="Maximum asking price" onChange={(e) => set({ max: e.target.value.replace(/\D/g, '') })} />
          </span></label>
        <label className="lc-f"><span>Beds</span>
          <select value={f.beds} onChange={(e) => set({ beds: e.target.value })}><option value="">Any</option>{[1, 2, 3, 4, 5].map((n) => <option key={n} value={n}>{n}+</option>)}</select></label>
        <label className="lc-f"><span>Baths</span>
          <select value={f.baths} onChange={(e) => set({ baths: e.target.value })}><option value="">Any</option>{[1, 2, 3].map((n) => <option key={n} value={n}>{n}+</option>)}</select></label>
        <label className="lc-f"><span>Area tier</span>
          <select value={f.tier} onChange={(e) => set({ tier: e.target.value })}><option value="">Any</option>
            {TIERS.slice(0, 4).map((t, i) => <option key={t} value={t}>{i ? `${t} or better` : 'S only'}</option>)}</select></label>
        <label className="lc-f"><span>Condition</span>
          <select value={f.cond} onChange={(e) => set({ cond: e.target.value })}><option value="">Any</option>
            {data.photoTagging && <option value="defects">Defects noted</option>}
            <option value="asis">As-is / needs work</option><option value="renovated">Renovated / new</option>
            {data.photoTagging && <option value="none">Photos not analysed</option>}</select></label>
        <label className="lc-f"><span>Vs estimate</span>
          <span className="lc-range">
            <select value={f.est} onChange={(e) => set({ est: e.target.value })}><option value="">Any</option>
              <option value="under">Asking under</option><option value="over">Asking over</option></select>
            {f.est && <select value={f.th} aria-label="By at least" onChange={(e) => set({ th: e.target.value })}>
              {['5', '10', '20'].map((t) => <option key={t} value={t}>by {t}%+</option>)}</select>}
          </span></label>
        <details className="lc-weights">
          <summary title={`Area tiers use: ${presetFor(w)?.label ?? 'custom weights'}`}>Tier weights</summary>
          <div className="lc-weights-body">
            <div className="segmented" role="group" aria-label="Weights">
              {PRESETS.map((p) => <button key={p.id} className={presetFor(w)?.id === p.id ? 'on' : ''} onClick={() => setW(p.w)}>{p.label}</button>)}
            </div>
            <div className="weights f-weights">
              {FACTORS.map((x) => <div className="weight" key={x.id}>
                <label className="weight-top" htmlFor={`lw-${x.id}`}><b>{x.label}</b><span>{w[x.id] === 0 ? 'off' : w[x.id]}</span></label>
                <input id={`lw-${x.id}`} type="range" min={0} max={5} step={1} value={w[x.id]}
                  onChange={(e) => { const nw = { ...w, [x.id]: +e.target.value }; if (IDS.some((id) => nw[id] > 0)) setW(nw) }} />
              </div>)}
            </div>
          </div>
        </details>
      </div>
    </section>

    <div className={`lc-split view-${view}`}>
      <div className="lc-mapcol">
        <ListingsMap center={city.center} zoom={city.zoom} pins={pins} active={active} focus={picked} onHover={setHover} onPick={(id) => { pick(id); openHome(id) }}
          height="var(--lc-map-h)" visible={view === 'map'} />
        <div className="map-legend lc-legend" aria-label="Pin style by days on market">
          <span><i className="lc-pin a0">$</i>&lt; 30 days</span><span><i className="lc-pin a1">$</i>30–90</span><span><i className="lc-pin a2">$</i>90+</span>
          <span><i className="lc-pin a0 off dot" />other pages</span>
        </div>
      </div>
      <div className="lc-list" ref={listRef}>
        {cards.map((c) => <ListingCard key={c.id} c={c} on={c.id === active} onHover={setHover} {...cardProps} />)}
        {!cards.length && <p className="muted">No homes match.</p>}
        {pages > 1 && <Pager page={page} pages={pages} go={goPage} />}
      </div>
    </div>

    {openCard && <ListingPanel c={openCard} city={city} area={areasDoc?.areas.find((a) => a.key === openCard.area?.key) ?? null}
      areas={areasDoc?.areas ?? []} tier={openCard.area ? tiers.get(openCard.area.key) : undefined} ramp={ramp}
      cityGrowth={data.cityFigures?.growth1 ?? null} photoCity={data.photoTagging} hidden={hiddenSet.has(openCard.id)}
      daysSince={daysSince} onHide={() => toggleHide(openCard.id)} onClose={closeHome} />}
    <p className="footnote">{data.photoTagging ? 'Photo notes come from an AI model and can be wrong.' : 'Photo analysis: Philadelphia only for now.'}</p>
  </div>
}

function Pager({ page, pages, go }: { page: number; pages: number; go: (p: number) => void }) {
  const nums = [...new Set([1, page - 1, page, page + 1, pages].filter((n) => n >= 1 && n <= pages))].sort((a, b) => a - b)
  return <nav className="lc-pager" aria-label="Pages">
    <button className="ghost small" disabled={page <= 1} onClick={() => go(page - 1)}>‹ Prev</button>
    {nums.map((n, i) => <span key={n} className="lc-pager-n">
      {i > 0 && n - nums[i - 1] > 1 && <span className="muted">…</span>}
      <button className={`ghost small ${n === page ? 'on' : ''}`} aria-current={n === page ? 'page' : undefined} onClick={() => go(n)}>{n}</button>
    </span>)}
    <button className="ghost small" disabled={page >= pages} onClick={() => go(page + 1)}>Next ›</button>
  </nav>
}

// ---- one card ----
interface CardProps {
  onOpen: (id: string) => void
  c: Card; on: boolean; onHover: (id: string | null) => void
  city: City; tiers: Map<string, Tier>; ramp: ReturnType<typeof useRamp>; daysSince: (d: string | null) => number
  hiddenSet: Set<string>; toggleHide: (id: string) => void; photoCity: boolean
}

function ListingCard({ c, on, onHover, city, tiers, ramp, daysSince, hiddenSet, toggleHide, photoCity, onOpen }: CardProps) {
  const t = c.area ? tiers.get(c.area.key) : undefined
  const fx = c.facts
  const facts = [fx.beds != null && `${fx.beds} bd`, fx.baths != null && `${fx.baths} ba`, fx.sqft && `${fx.sqft.toLocaleString('en-US')} sq ft`,
    fx.homeType ?? fx.type].filter(Boolean).join(' · ')
  const isHidden = hiddenSet.has(c.id)
  const stale = c.lastSeen && daysSince(c.lastSeen) > STALE_DAYS
  return <article className={`lc-card ${on ? 'on' : ''} ${isHidden ? 'is-hidden' : ''}`} data-id={c.id}
    onMouseEnter={() => onHover(c.id)} onMouseLeave={() => onHover(null)}
    onClick={(ev) => { if (!(ev.target as HTMLElement).closest('a, button, input, select, .lc-pop')) onOpen(c.id) }}>
    <div className="lc-top">
      <div>
        <div className="lc-price"><small>Asking</small> {c.price ? money(c.price) : 'not given'}</div>
        <div className="muted lc-small">{c.date ? <>Listed {shortDate(c.date)} · {ago(daysSince(c.date))}</> : 'List date not given'}</div>
        {stale && <div className="muted lc-small">Last seen {shortDate(c.lastSeen!)} — may be off the market</div>}
      </div>
      <Headline c={c} city={city} />
    </div>
    <div className="lc-addr"><button className="lc-open" onClick={() => onOpen(c.id)}>{c.address ?? 'Address not given'}</button>
      <a className="lc-ext" href={safeUrl(c.url)} target="_blank" rel="noopener noreferrer" title={`Open on ${c.links[0]?.source ?? 'the publisher'}`} aria-label="Open the publisher's listing">↗</a></div>
    {facts && <div className="muted lc-small">{facts}</div>}
    {c.area && <div className="lc-area">
      {t ? <span className="f-tier" style={tierStyle(t, ramp)} aria-hidden="true">{t}</span> : null}
      <span>{t ? `Tier ${t} · ` : ''}{c.area.name}</span>
      <a className="lc-small" href={`#/${city.id}/areas?area=${encodeURIComponent(c.area.key)}`}>Detailed View ›</a>
    </div>}
    <PlusMinus c={c} />
    {(c.lastSale || c.tags?.length) ? <div className="lc-tags">
      {c.lastSale && <span className="chip" title="Latest recorded sale of this address">Sold {usdCompact(c.lastSale.price)} in {monthYear(c.lastSale.date)}</span>}
      {c.tags?.map((x) => <span key={x} className="chip">{x}</span>)}
    </div> : null}
    {photoCity && c.photo && <Photos c={c} />}
    <div className="lc-foot">
      <span className="lc-small">{c.links.map((l, i) => <span key={l.url}>{i ? ' · ' : ''}<a href={safeUrl(l.url)} target="_blank" rel="noopener noreferrer">{l.source ?? 'Listing'} ↗</a></span>)}</span>
      <button className="lc-link" onClick={() => toggleHide(c.id)}>{isHidden ? 'Unhide' : 'Hide'}</button>
    </div>
  </article>
}

function PlusMinus({ c }: { c: Card }) {
  const [all, setAll] = useState(false)
  // the home itself first (photos, description, price model), then the price against sales, then the area
  const order = (k: Item['kind']) => ['photo', 'description', 'model', 'sold', 'area'].indexOf(k)
  const byKind = (a: Item, b: Item) => order(a.kind) - order(b.kind)
  const plus = [...c.plus].sort(byKind), minus = [...c.minus].sort(byKind)
  const rows = [...(all ? plus : plus.slice(0, SHOWN_ITEMS)).map((x) => ({ ...x, s: '+' as const })),
    ...(all ? minus : minus.slice(0, SHOWN_ITEMS)).map((x) => ({ ...x, s: '−' as const }))]
  const more = plus.length + minus.length - rows.length
  if (!rows.length) return null
  return <ul className="lc-pm">
    {rows.map((r, i) => <li key={i} className={r.s === '+' ? 'plus' : 'minus'} title={r.why}>
      <b aria-label={r.s === '+' ? 'plus' : 'minus'}>{r.s}</b><span>{r.text}</span></li>)}
    {more > 0 && <li><button className="lc-link" onClick={() => setAll(true)}>+{more} more</button></li>}
  </ul>
}

function Photos({ c }: { c: Card }) {
  const p = c.photo!
  const href = safeUrl(p.creditUrl ?? c.url)
  const credit = p.credit ?? c.source ?? 'the publisher'
  return <div className="lc-photos">
    <span className="lc-small" title="Photos can show a problem; they can't rule one out.">{p.findings ? `${p.findings} ${p.findings === 1 ? 'defect' : 'defects'} in photos` : 'No defects seen in photos'}{p.findings && p.allLow ? <> <WarnMark /></> : null}</span>
    {p.crops.length > 0 && <span className="lc-crops">
      {p.crops.slice(0, 5).map((x) => <a key={x.src} href={href} target="_blank" rel="noopener noreferrer" title={`${x.label}${x.low ? ` (${LOW_MSG.toLowerCase()})` : ''}. Crop of a ${credit} photo; opens the listing.`}>
        <CropImage src={x.src} alt={`${x.label}, detected in a listing photo`} mark={x.mark} point={x.point} low={x.low} cover /></a>)}
      {p.crops.length > 5 && <a className="lc-more" href={href} target="_blank" rel="noopener noreferrer" title={`More crops of ${credit} photos; opens the listing.`}>+{p.crops.length - 5}</a>}
    </span>}
  </div>
}

// ---- the headline number and its breakdown ----
function Headline({ c, city }: { c: Card; city: City }) {
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
  const e = c.price ? c.estimate : undefined
  const v = e ? Math.round(e.vsPct) : 0
  const within = e && e.error != null && Math.abs(e.vsPct) <= e.error
  const what = !e ? '' : e.kind === 'model' ? `est. ${usdCompact(e.value)}` : e.kind === 'ppsf' ? 'area $/sq ft' : 'area median'
  return <div className="lc-head-num" ref={box} onMouseEnter={() => setOpen(true)} onMouseLeave={() => !pinned && setOpen(false)}>
    <button className={`lc-score ${!e ? 'none' : within ? 'within' : v < 0 ? 'under' : 'over'}`} aria-expanded={open}
      onClick={() => { setPinned(!pinned); setOpen(!pinned) }} onFocus={() => setOpen(true)} onBlur={() => !pinned && setOpen(false)}>
      {e ? <><b>{v === 0 ? '≈ same' : `${Math.abs(v)}% ${v < 0 ? 'under' : 'over'}`}</b><small>{what}</small></> : <><b>No estimate</b><small>why?</small></>}
    </button>
    {open && <div className="lc-pop" role="dialog" aria-label="About this number">
      {e ? <Breakdown c={c} e={e} city={city} /> : <p>{c.noEstimate ?? 'Not enough to compare.'}</p>}
    </div>}
  </div>
}
