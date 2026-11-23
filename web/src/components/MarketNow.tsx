import '../styles/listings.css'
import { ReactNode } from 'react'
import { Listings } from '../lib/data'
import { monthLabel, num, usdCompact } from '../lib/format'
import Sparkline from '../charts/Sparkline'
import { Delta } from './bits'

/**
 * "Market now": asking-side listing figures from Zillow Research, by ZIP code.
 * Kept in its own marked box so it is never read as, or charted with, recorded sales.
 */
export default function MarketNow({ l, area, wide }: {
  l: Listings | null | undefined
  area?: boolean               // area figures are ZIP figures mapped onto the area
  wide?: boolean               // the citywide strip on the Market page
}) {
  if (!l) return null
  const yearAgo = monthLabel(`${+l.month.slice(0, 4) - 1}${l.month.slice(4)}`)
  const countTip = area ? "Estimated: each ZIP code's count, shared out by how many of its recent sales are in this area" : undefined
  return (
    <section className={`ask ${wide ? 'wide' : ''}`} aria-label={`Market now, listings, ${monthLabel(l.month)}`}>
      <header className="ask-head">
        <span className="ask-title">Market now · listings, {monthLabel(l.month)}</span>
        <span className="chip ask-chip" title="Asking-side data from listings, not recorded sales">Asking</span>
      </header>
      <div className="ask-grid">
        <Cell label="Active listings" value={num(l.active)} tip={countTip}
          extra={l.series?.active ? <Sparkline values={l.series.active} width={72} height={20} /> : null}>
          <Delta value={l.activeYoY} short />
        </Cell>
        <Cell label="New listings" value={num(l.newListings)} tip={countTip}>
          <Delta value={l.newListingsYoY} short />
        </Cell>
        <Cell label="Median list price" value={usdCompact(l.listPrice)}
          >
          <Delta value={l.listPriceYoY} short />
        </Cell>
        <Cell label="With a price cut" value={l.priceCutShare == null ? '–' : `${l.priceCutShare.toFixed(0)}%`}>
          <Delta value={l.priceCutShareYoY} points short />
        </Cell>
      </div>
      <p className="ask-foot">
        Change vs {yearAgo} · Listings: Zillow Research, by ZIP{l.coverage < 100 ? ` · ${l.coverage}% of sales in covered ZIPs` : ''}
      </p>
    </section>
  )
}

function Cell({ label, value, tip, extra, children }: { label: string; value: string; tip?: string; extra?: ReactNode; children: ReactNode }) {
  return (
    <div className="ask-cell" title={tip}>
      <span className="ask-label">{label}</span>
      <span className="ask-value">{value}{tip && value !== '–' && <span className="ask-est" aria-label="estimated">est.</span>}</span>
      <span className="ask-delta">{children}{extra}</span>
    </div>
  )
}
