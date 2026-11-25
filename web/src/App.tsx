import { lazy, Suspense, useEffect, useState } from 'react'
import './styles/nav.css'
import { City, Index, Status, useData } from './lib/data'
import { useRoute, useTheme } from './lib/hooks'
import { monthLabel, pct, timeAgo, usd } from './lib/format'
import { site } from './site'
import Sparkline from './charts/Sparkline'

// Each page is its own chunk, fetched when it is first opened (DuckDB-WASM only ever loads from inside Explore).
const PAGES = {
  market: () => import('./pages/Market'),
  drivers: () => import('./pages/Drivers'),
  areas: () => import('./pages/Areas'),
  sold: () => import('./pages/Sold'),
  about: () => import('./pages/About'),
  listings: () => import('./pages/NewListings'),
  compare: () => import('./pages/Compare'),
  find: () => import('./pages/Screener'),
  pipeline: () => import('./pages/Pipeline'),
  explore: () => import('./pages/Explore'),
} as const
type PageId = keyof typeof PAGES
const preload = (id: string) => { if (id in PAGES) PAGES[id as PageId]().catch(() => {}) }
const Market = lazy(PAGES.market)
const Drivers = lazy(PAGES.drivers)
const AreasPage = lazy(PAGES.areas)
const SoldPage = lazy(PAGES.sold)
const About = lazy(PAGES.about)
const NewListings = lazy(PAGES.listings)
const ComparePage = lazy(PAGES.compare)
const ScreenerPage = lazy(PAGES.find)
const Pipeline = lazy(PAGES.pipeline)
const Explore = lazy(PAGES.explore)
// The page being opened starts downloading alongside the app and index.json, not after them.
{
  const at = window.location.hash.replace(/^#\/?/, '').split('?')[0].split('/').filter(Boolean)
  const first = at[0] === 'pipeline' || at[0] === 'about' ? at[0] : at[1] ?? 'find'
  preload(first === 'sales' ? 'sold' : first)
}

const ICON: Record<string, string> = {
  market: 'M3 17l5-6 4 3 6-8M3 21h18',
  areas: 'M12 21s-7-6.2-7-11a7 7 0 0114 0c0 4.800-7 11-7 11zm0-8.500a2.500 2.500 0 100-5 2.500 2.500 0 000 5z',
  listings: 'M3 11l9-8 9 8M5 10v11h14V10M9 21v-7h6v7',
  sold: 'M4 6h16M4 12h16M4 18h10',
  pipeline: 'M4 6h5v5H4zM15 13h5v5h-5zM9 8.500h3.500a2.500 2.500 0 012.500 2.500v2',
  find: 'M4 5h16l-6 7v6l-4 2v-8z',
  compare: 'M12 3v18M4 6h5v12H4zM15 6h5v12h-5z',
  drivers: 'M4 6h10M4 12h16M4 18h6',
  explore: 'M4 17l5-5-5-5M12 19h8',
  about: 'M12 21a9 9 0 100-18 9 9 0 000 18zM12 16v-5M12 8h.01',
}

function Item({ id, label, href, on }: { id: string; label: string; href: string; on: boolean }) {
  return (
    <a className={`item ${on ? 'on' : ''}`} href={href} aria-current={on ? 'page' : undefined} onPointerEnter={() => preload(id)} onFocus={() => preload(id)}>
      <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={ICON[id]} /></svg>
      {label}
    </a>
  )
}

/** Pages that take a city. Old hashes keep working: #/city/sales now opens Sold prices. */
const CITY_PAGES = ['find', 'areas', 'listings', 'compare', 'market', 'sold', 'drivers', 'explore']
const ALIAS: Record<string, string> = { sales: 'sold' }
const MAIN = [['find', 'Find areas'], ['listings', 'New listings']]
const RESEARCH = [['areas', 'Neighborhoods'], ['compare', 'Crime'], ['market', 'Market'], ['sold', 'Sold prices'], ['drivers', 'Price drivers'], ['explore', 'Explore']]
const LABEL: Record<string, string> = Object.fromEntries([...MAIN, ...RESEARCH, ['pipeline', 'Data Engine'], ['about', 'About']])

export default function App() {
  const route = useRoute()
  const [theme, toggleTheme] = useTheme()
  const [menu, setMenu] = useState(false)
  const { data: index, error } = useData<Index>('index.json')
  const { data: status } = useData<Status>('status.json')
  useEffect(() => setMenu(false), [route.city, route.page])

  const cities = index?.cities ?? []
  const city: City | undefined = cities.find((c) => c.id === route.city) ?? cities[0]
  const asked = ALIAS[route.page] ?? route.page
  const global = asked === 'about' || (route.city === null && asked === 'pipeline')
  const page = global ? asked : CITY_PAGES.includes(asked) ? asked : 'find'
  const last = status?.last
  const warnings = last?.checks.filter((c) => c.status === 'warn').length ?? 0
  const noun = city?.areaNoun.toLowerCase() ?? 'area'
  const title = { market: 'housing market', drivers: 'price drivers', areas: `by ${noun}`, sold: `sold prices by ${noun}`, explore: 'sales explorer', listings: 'new listings', compare: 'reported incidents', find: 'area finder' }[page] ?? ''
  const href = (id: string) => (id === 'pipeline' || id === 'about' ? `#/${id}` : `#/${city?.id ?? ''}/${id}`)
  const item = ([id, label]: string[]) => <Item key={id} id={id} label={label} href={href(id)} on={page === id} />
  const inMore = !MAIN.some(([id]) => id === page)

  return (
    <div className="shell">
      <aside className={`side ${menu ? 'menu-open' : ''}`}>
        <div className="side-top">
          <a className="brand" href="#/">
            <svg viewBox="0 0 32 32" width="22" height="22" aria-hidden="true"><path d="M3 15.500L16 4l13 11.500" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" /><path d="M7 28v-8h4v8zM14 28V15h4v13zM21 28V18h4v10z" fill="currentColor" /></svg>
            REALIST
          </a>
          <button className={`menu-btn ${inMore ? 'on' : ''}`} aria-expanded={menu} aria-controls="side-more" onClick={() => setMenu(!menu)}>
            {inMore ? LABEL[page] : 'More'}
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" aria-hidden="true"><path d={menu ? 'M6 6l12 12M18 6L6 18' : 'M4 7h16M4 12h16M4 17h16'} /></svg>
          </button>
        </div>
        <nav className="side-main" aria-label="Main">
          {MAIN.map(item)}
        </nav>
        <div className="side-more" id="side-more">
          <div className="side-label">Research</div>
          <nav className="side-group" aria-label="Research">{RESEARCH.map(item)}</nav>
          <div className="side-label">Site</div>
          <nav className="side-group" aria-label="Site">{item(['pipeline', 'Data Engine'])}{item(['about', 'About'])}</nav>
          <div className="side-foot">
          {last && (
            <a className="status" href="#/pipeline">
              <i className={`dot ${!last.ok ? 'bad' : warnings ? 'warn' : 'ok'}`} />
              <span>
                Pipeline {!last.ok ? 'failed its checks' : warnings ? `passed, ${warnings} warning${warnings > 1 ? 's' : ''}` : 'healthy'}<br />
                <span className="muted">Last run {timeAgo(last.finished)}</span>
              </span>
            </a>
          )}
          <div className="segmented theme-toggle" role="group" aria-label="Colour theme">
            {(['dark', 'light'] as const).map((t) => (
              <button key={t} className={t === theme ? 'on' : ''} aria-pressed={t === theme} onClick={() => t !== theme && toggleTheme()}>
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
                  <path d={t === 'dark' ? 'M20 14.500A8 8 0 019.500 4 8 8 0 1020 14.500z' : 'M12 16a4 4 0 100-8 4 4 0 000 8zM12 2v2m0 16v2M4.900 4.900l1.400 1.400m11.400 11.400l1.400 1.400M2 12h2m16 0h2M4.900 19.100l1.400-1.400M17.700 6.300l1.400-1.400'} />
                </svg>
                {t === 'dark' ? 'Dark' : 'Light'}
              </button>
            ))}
          </div>
          </div>
        </div>
      </aside>

      <main className="main">
        {error && <div className="notice bad">The data files could not be loaded. {error}</div>}
        {!index && !error && <div className="loading">Loading market data…</div>}
        {/* One fallback for the first load, the same placeholder as for index.json, so the page arrives in the same
            steps as before code splitting (no extra layout shift); later route changes keep the old page up until the
            next one's chunk is in (useRoute switches routes in a transition). */}
        <Suspense fallback={index && <div className="loading">Loading market data…</div>}>
        {index && !global && city && (
          <>
            <nav className="ticker" aria-label="City">
              {cities.map((c) => (
                <a key={c.id} href={`#/${c.id}/${page}`} className={c.id === city.id ? 'on' : ''} aria-current={c.id === city.id ? 'true' : undefined}>
                  <small>{c.name}</small>
                  <b>{usd(c.kpi.median)}<em>{c.kpi.medianYoY == null ? '' : `${c.kpi.medianYoY > 0 ? '▲' : '▼'} ${pct(Math.abs(c.kpi.medianYoY)).replace('+', '')}`}</em></b>
                  <Sparkline values={c.spark} width={88} height={30} />
                </a>
              ))}
            </nav>
            <div className="page-head">
              <div>
                <h1>{city.name} {title}</h1>
                {page !== 'listings' && <p className="muted">
                  {city.rows.toLocaleString('en-US')} recorded home sales since {city.firstSale.slice(0, 4)}. Statistics run through {monthLabel(city.through)}, the latest month with complete public records.
                </p>}
              </div>
            </div>
            {page === 'market' && <Market key={city.id} city={city} />}
            {page === 'drivers' && <Drivers key={city.id} city={city} />}
            {page === 'areas' && <AreasPage key={city.id} city={city} theme={theme} />}
            {page === 'listings' && <NewListings key={city.id} city={city} />}
            {page === 'sold' && <SoldPage key={city.id} city={city} />}
            {page === 'explore' && <Explore key={city.id} city={city} />}
            {page === 'compare' && <ComparePage key={city.id} city={city} theme={theme} />}
            {page === 'find' && <ScreenerPage key={city.id} city={city} theme={theme} />}
          </>
        )}
        {index && status && page === 'pipeline' && global && <Pipeline index={index} status={status} />}
        {page === 'about' && <About />}
        </Suspense>

        <footer className="footer">
          <span>Built from open public records. Figures are for research, not appraisal or financial advice.</span>
          <span>Built by <a href={site.authorUrl} target="_blank" rel="noopener noreferrer">{site.author}</a></span>
        </footer>
      </main>
    </div>
  )
}
