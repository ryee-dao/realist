import { City, Index, Status, useData } from './lib/data'
import { useRoute, useTheme } from './lib/hooks'
import { monthLabel, pct, timeAgo, usd } from './lib/format'
import { site } from './site'
import Sparkline from './charts/Sparkline'
import Market from './pages/Market'
import AreasPage from './pages/Areas'
import SalesPage from './pages/Sales'
import NewListings from './pages/NewListings'
import ComparePage from './pages/Compare'
import ScreenerPage from './pages/Screener'
import Pipeline from './pages/Pipeline'
import About from './pages/About'

const ICON: Record<string, string> = {
  market: 'M3 17l5-6 4 3 6-8M3 21h18',
  areas: 'M12 21s-7-6.2-7-11a7 7 0 0114 0c0 4.800-7 11-7 11zm0-8.500a2.500 2.500 0 100-5 2.500 2.500 0 000 5z',
  listings: 'M3 11l9-8 9 8M5 10v11h14V10M9 21v-7h6v7',
  sales: 'M4 6h16M4 12h16M4 18h10',
  pipeline: 'M4 6h5v5H4zM15 13h5v5h-5zM9 8.500h3.500a2.500 2.500 0 012.500 2.500v2',
  find: 'M4 5h16l-6 7v6l-4 2v-8z',
  compare: 'M12 3v18M4 6h5v12H4zM15 6h5v12h-5z',
  about: 'M12 21a9 9 0 100-18 9 9 0 000 18zm0-9v5m0-8.500v.010',
}

function Item({ id, label, href, on }: { id: string; label: string; href: string; on: boolean }) {
  return (
    <a className={`item ${on ? 'on' : ''}`} href={href} aria-current={on ? 'page' : undefined}>
      <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={ICON[id]} /></svg>
      {label}
    </a>
  )
}

export default function App() {
  const route = useRoute()
  const [theme, toggleTheme] = useTheme()
  const { data: index, error } = useData<Index>('index.json')
  const { data: status } = useData<Status>('status.json')

  const cities = index?.cities ?? []
  const city: City | undefined = cities.find((c) => c.id === route.city) ?? cities[0]
  const global = route.city === null && (route.page === 'pipeline' || route.page === 'about')
  const page = global ? route.page : ['market', 'areas', 'sales', 'listings', 'compare', 'find'].includes(route.page) ? route.page : 'find'
  const last = status?.last
  const warnings = last?.checks.filter((c) => c.status === 'warn').length ?? 0
  const title = { market: 'housing market', areas: `by ${city?.areaNoun.toLowerCase()}`, sales: 'recent home sales', listings: 'new listings', compare: 'reported incidents', find: 'area finder' }[page] ?? ''

  return (
    <div className="shell">
      <aside className="side">
        <a className="brand" href="#/">
          <svg viewBox="0 0 32 32" width="22" height="22" aria-hidden="true"><path d="M6 25V13h4v12zm7 0V6h4v19zm7 0v-9h4v9z" fill="currentColor" /></svg>
          REALIST
        </a>
        <div className="side-label">Explore</div>
        <Item id="find" label="Find areas" href={`#/${city?.id ?? ''}/find`} on={page === 'find'} />
        <Item id="areas" label="Areas" href={`#/${city?.id ?? ''}/areas`} on={page === 'areas'} />
        <Item id="market" label="Market" href={`#/${city?.id ?? ''}/market`} on={page === 'market'} />
        <Item id="compare" label="Crime" href={`#/${city?.id ?? ''}/compare`} on={page === 'compare'} />
        <Item id="listings" label="New listings" href={`#/${city?.id ?? ''}/listings`} on={page === 'listings'} />
        <Item id="sales" label="Recent sales" href={`#/${city?.id ?? ''}/sales`} on={page === 'sales'} />
        <div className="side-label">Engine</div>
        <Item id="pipeline" label="Data pipeline" href="#/pipeline" on={page === 'pipeline'} />
        <Item id="about" label="About" href="#/about" on={page === 'about'} />
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
      </aside>

      <main className="main">
        {error && <div className="notice bad">The data files could not be loaded. {error}</div>}
        {!index && !error && <div className="loading">Loading market data…</div>}
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
            {page === 'areas' && <AreasPage key={city.id} city={city} theme={theme} />}
            {page === 'listings' && <NewListings key={city.id} city={city} />}
            {page === 'sales' && <SalesPage key={city.id} city={city} theme={theme} />}
            {page === 'compare' && <ComparePage key={city.id} city={city} theme={theme} />}
            {page === 'find' && <ScreenerPage key={city.id} city={city} theme={theme} />}
          </>
        )}
        {index && status && page === 'pipeline' && global && <Pipeline index={index} status={status} />}
        {index && page === 'about' && global && <About index={index} />}

        <footer className="footer">
          <span>Built from open public records. Figures are for research, not appraisal or financial advice.</span>
          <span>Built by <a href={site.authorUrl}>{site.author}</a> · <a href={site.repoUrl}>Source on GitHub</a></span>
        </footer>
      </main>
    </div>
  )
}
