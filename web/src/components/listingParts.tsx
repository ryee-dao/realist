// Types and small visuals shared by the New listings page and its detail panel.
import { City } from '../lib/data'
import { usdCompact } from '../lib/format'

// ---- engine/listing_cards.py output ----
export type Item = { text: string; kind: 'model' | 'area' | 'sold' | 'description' | 'photo'; why?: string }
export type Part = { label: string; pct: number; driver: string; notRecorded?: boolean }
export type Estimate = {
  kind: 'model' | 'ppsf' | 'median'; value: number; vsPct: number; low?: number; high?: number
  error: number | null; errorBand?: string | null
  base?: number; area?: { label: string; pct: number }; parts?: Part[]
  ppsf?: number; sqft?: number
}
export type Card = {
  id: string; url: string; address: string | null; date: string | null; price: number | null; source: string | null
  links: { source: string | null; url: string }[]
  facts: { beds?: number; baths?: number; sqft?: number; type?: string; homeType?: string }
  lat?: number; lon?: number; inSnapshot?: false; firstSeen?: string; lastSeen?: string
  area?: { key: string; name: string; mapping: string }
  sold?: { ppsf12?: number; median12?: number; sales12?: number; vsPpsfPct?: number; vsMedianPct?: number; topType?: string; topTypeShare?: number }
  estimate?: Estimate; noEstimate?: string
  lastSale?: { date: string; price: number; recentResale?: boolean }
  plus: Item[]; minus: Item[]; tags?: string[]; signals?: string[]
  detail?: string   // <city>/listing-detail/<detail>.json
  photo?: { findings: number; crops: { src: string; label: string }[]; credit: string | null; creditUrl: string | null
    condition: string | null; coverage: string | null; seen: string | null; possiblyEdited: boolean }
}
export type CardsDoc = {
  city: string; asOf: string | null; from: string | null; through: string | null
  model: { error: number | null; ppsfError: number | null } | null
  photoTagging: boolean
  cityFigures?: { growth1: number | null }
  summary: { cards: number }
  cards: Card[]
}

export const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
export const shortDate = (d: string) => `${MONTHS[+d.slice(5, 7) - 1]} ${+d.slice(8, 10)}`
export const monthYear = (d: string) => `${MONTHS[+d.slice(5, 7) - 1]} ${d.slice(0, 4)}`
export const money = (n: number) => '$' + Math.round(n).toLocaleString('en-US')

export function Bar({ pct, max }: { pct: number; max: number }) {
  const w = Math.min(50, (Math.abs(pct) / max) * 50)
  return <span className="lc-bar-track" aria-hidden="true"><i className={pct >= 0 ? 'up' : 'down'} style={pct >= 0 ? { left: '50%', width: `${w}%` } : { right: '50%', width: `${w}%` }} /></span>
}

/** Two values side by side as plain bars from zero (asking against what sold). */
export function Pair({ rows }: { rows: { label: string; value: number; text: string; asking?: boolean }[] }) {
  const max = Math.max(...rows.map((r) => r.value)) || 1
  return <div className="lc-rows">
    {rows.map((r) => <div className="lc-row pair" key={r.label}><span>{r.label}</span>
      <span className="lc-bar-track plain" aria-hidden="true"><i className={r.asking ? 'ask' : 'sold'} style={{ left: 0, width: `${(r.value / max) * 100}%` }} /></span>
      <span className="lc-v">{r.text}</span></div>)}
  </div>
}

export function Breakdown({ c, e, city, max: most = 5 }: { c: Card; e: Estimate; city: City; max?: number }) {
  const err = e.error != null ? `Usually within ±${Math.round(e.error)}%.` : 'Untested here.'
  const drivers = city.id === 'philadelphia' ? <> <a href={`#/${city.id}/drivers`}>Price drivers ›</a></> : null
  if (e.kind === 'model') {
    const parts = [e.area!, ...(e.parts ?? []).filter((p) => !p.notRecorded && p.driver !== 'other')]
      .filter((p) => Math.abs(p.pct) >= 3).sort((a, b) => Math.abs(b.pct) - Math.abs(a.pct)).slice(0, most)
    const max = Math.max(20, ...parts.map((r) => Math.abs(r.pct)))
    return <>
      <p className="lc-pop-title">About {usdCompact(e.value)}{e.low && e.high ? <span className="muted"> · {usdCompact(e.low)}–{usdCompact(e.high)}</span> : null}</p>
      <div className="lc-rows">
        <div className="lc-row base"><span>Typical home</span><span className="lc-v">{usdCompact(e.base)}</span></div>
        {parts.map((r) => <div className="lc-row" key={r.label}><span>{r.label}</span><Bar pct={r.pct} max={max} />
          <span className="lc-v">{r.pct > 0 ? '+' : '−'}{Math.abs(Math.round(r.pct))}%</span></div>)}
      </div>
      <p className="muted lc-small">{err}{c.lastSale?.recentResale ? ' Record may predate recent work.' : ''} Not an appraisal.{drivers}</p>
    </>
  }
  if (e.kind === 'ppsf') {
    const ask = c.price && e.sqft ? c.price / e.sqft : 0
    return <>
      <p className="lc-pop-title">About {usdCompact(e.value)} at the area’s $/sq ft</p>
      <Pair rows={[{ label: 'Asking', value: ask, text: `$${Math.round(ask)}/sq ft`, asking: true },
        { label: 'Sold here', value: e.ppsf ?? 0, text: `$${e.ppsf}/sq ft` }]} />
      <p className="muted lc-small">{err} Size only.{drivers}</p>
    </>
  }
  return <>
    <p className="lc-pop-title">Area median {usdCompact(e.value)}</p>
    <Pair rows={[{ label: 'Asking', value: c.price ?? 0, text: usdCompact(c.price), asking: true },
      { label: 'Sold here', value: e.value, text: usdCompact(e.value) }]} />
    <p className="muted lc-small">Ignores size. Rough guide.</p>
  </>
}
