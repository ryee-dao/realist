// The listing detail panel: slides in over the map and cards (a full-screen sheet on phones).
import '../styles/areas.css'
import { ReactNode, useEffect, useMemo, useRef, useState } from 'react'
import { Area, City, useData } from '../lib/data'
import { Ramp, Tier, tierStyle } from '../lib/grades'
import { pct, usd, usdCompact } from '../lib/format'
import { rentLines, rentValue, yieldRange } from '../lib/rent'
import { Breakdown, Card, CropImage, LOW_MSG, Mark, Point, money, monthYear, Pair, shortDate, WarnMark } from './listingParts'

/** <city>/listing-detail/<name>.json, written by engine/listing_cards.py and loaded on first open. */
export interface Detail {
  id: string
  quotes?: { signal: string; label: string; quote: string }[]
  photo?: {
    findings: { type: string; label: string; room: string; rooms: number; confidence: number | null; evidence: string | null; crops: string[]
      lowConfidence?: boolean; caption?: string; marks?: Mark[]; points?: Point[] }[]
    missing: string[]; coverage: string | null; analysed: number | null; labelled: string[]; possiblyEdited: boolean
    condition: string | null; credit: string | null; creditUrl: string | null
  }
  record?: { yearBuilt?: number; assessed?: number }
  comps?: { address: string; price: number; date: string; sqft?: number; type?: string; km?: number }[]
}

const lower = (s: string) => s.charAt(0).toLowerCase() + s.slice(1)
type Tab = 'overview' | 'photos' | 'price' | 'area'
type Crop = { src: string; caption: string; evidence: string | null; short?: string; low?: boolean; mark?: Mark; point?: Point }

interface Props {
  c: Card; city: City; area: Area | null; areas: Area[]; tier: Tier | undefined; ramp: Ramp
  cityGrowth: number | null; photoCity: boolean; hidden: boolean; daysSince: (d: string | null) => number
  onHide: () => void; onClose: () => void
}

export default function ListingPanel({ c, city, area, areas, tier, ramp, cityGrowth, photoCity, hidden, daysSince, onHide, onClose }: Props) {
  const { data: detail } = useData<Detail>(c.detail ? `${city.id}/listing-detail/${c.detail}.json` : null)
  const [tab, setTab] = useState<Tab>('overview')
  const [zoom, setZoom] = useState<number | null>(null)
  const panel = useRef<HTMLElement>(null)
  const closeBtn = useRef<HTMLButtonElement>(null)

  // focus moves into the panel and returns to where it was on close
  useEffect(() => {
    const back = document.activeElement as HTMLElement | null
    closeBtn.current?.focus()
    return () => back?.focus?.()
  }, [])
  useEffect(() => { setTab('overview'); panel.current?.scrollTo(0, 0) }, [c.id])
  useEffect(() => {
    const key = (e: KeyboardEvent) => { if (e.key === 'Escape' && zoom == null) onClose() }
    document.addEventListener('keydown', key)
    return () => document.removeEventListener('keydown', key)
  }, [zoom, onClose])

  const crops: Crop[] = useMemo(() => (detail?.photo?.findings ?? []).flatMap((f) => f.crops.map((src, i) => ({
    src, caption: `${f.label}${f.room ? ` · ${f.room}` : ''}`, evidence: f.evidence, short: f.caption,
    low: f.lowConfidence, mark: f.marks?.[i] ?? null, point: f.points?.[i] }))), [detail])
  const e = c.price ? c.estimate : undefined
  const v = e ? Math.round(e.vsPct) : 0
  const publisher = c.links[0]?.source ?? c.source ?? 'the publisher'
  const findingCount = c.photo?.findings ?? 0

  return <div className="lp-layer" onMouseDown={(ev) => { if (ev.target === ev.currentTarget) onClose() }}>
    <aside className="lp" ref={panel} role="dialog" aria-modal="true" aria-label={`Listing: ${c.address ?? 'home for sale'}`}>
      <header className="lp-head">
        <div className="lp-top">
          <div>
            <div className="lc-price"><small>Asking</small> {c.price ? money(c.price) : 'not given'}</div>
            <div className={`lp-verdict ${!e ? 'none' : v < 0 ? 'under' : 'over'}`}>
              {e ? `${v === 0 ? 'About the same as' : `${Math.abs(v)}% ${v < 0 ? 'under' : 'over'}`} ${e.kind === 'model' ? 'estimate' : e.kind === 'ppsf' ? 'area $/sq ft' : 'area median'}`
                : <>No estimate — <span className="muted">{lower(c.noEstimate ?? 'not enough to compare')}</span></>}
            </div>
          </div>
          <button ref={closeBtn} className="ghost small" onClick={onClose} aria-label="Close">✕ Close</button>
        </div>
        <div className="lp-addr">{c.address ?? 'Address not given'}</div>
        {c.area && <div className="lc-area">
          {tier && <span className="f-tier" style={tierStyle(tier, ramp)} aria-hidden="true">{tier}</span>}
          <span>{tier ? `Tier ${tier} · ` : ''}{c.area.name}</span>
        </div>}
        <div className="lp-tabs" role="tablist">
          {([['overview', 'Overview'], ['photos', photoCity && c.photo ? `Photos (${findingCount})` : 'Photos'], ['price', 'Price'], ['area', 'Area']] as [Tab, string][]).map(([id, label]) =>
            <button key={id} role="tab" aria-selected={tab === id} className={tab === id ? 'on' : ''} onClick={() => setTab(id)}>{label}</button>)}
        </div>
      </header>

      <div className="lp-body">
        {tab === 'overview' && <Overview c={c} detail={detail} daysSince={daysSince} publisher={publisher} />}
        {tab === 'photos' && <Photos c={c} detail={detail} photoCity={photoCity} crops={crops} open={setZoom} publisher={publisher} />}
        {tab === 'price' && <Price c={c} detail={detail} city={city} />}
        {tab === 'area' && <AreaTab c={c} area={area} areas={areas} city={city} tier={tier} ramp={ramp} cityGrowth={cityGrowth} />}
      </div>

      <footer className="lp-foot">
        <a className="lp-go" href={c.url} target="_blank" rel="noopener noreferrer">View on {publisher} ↗</a>
        <button className="lc-link" onClick={onHide}>{hidden ? 'Unhide' : 'Hide'}</button>
      </footer>
    </aside>
    {zoom != null && crops[zoom] && <Lightbox crops={crops} at={zoom} go={setZoom} credit={detail?.photo?.credit ?? publisher}
      href={detail?.photo?.creditUrl ?? c.url} />}
  </div>
}

function Section({ title, children }: { title?: string; children: ReactNode }) {
  return <section className="lp-sec">{title && <h4>{title}</h4>}{children}</section>
}

function Overview({ c, detail, daysSince, publisher }: { c: Card; detail: Detail | null; daysSince: (d: string | null) => number; publisher: string }) {
  const f = c.facts
  const facts: [string, string][] = ([
    ['Beds', f.beds != null ? String(f.beds) : null], ['Baths', f.baths != null ? String(f.baths) : null],
    ['Size', f.sqft ? `${f.sqft.toLocaleString('en-US')} sq ft` : null],
    ['$/sq ft', f.sqft && c.price ? `$${Math.round(c.price / f.sqft)}` : null],
    ['Built', detail?.record?.yearBuilt ? String(detail.record.yearBuilt) : null], ['Type', f.homeType ?? f.type ?? null],
  ] as [string, string | null][]).filter((x): x is [string, string] => x[1] != null)
  const rows = [...c.plus.map((x) => ({ ...x, s: '+' })), ...c.minus.map((x) => ({ ...x, s: '−' }))]
  return <>
    {facts.length > 0 && <div className="lp-facts">{facts.map(([k, v]) => <div key={k}><span>{k}</span><b>{v}</b></div>)}</div>}
    {rows.length > 0 && <Section><ul className="lc-pm">
      {rows.map((r, i) => <li key={i} className={r.s === '+' ? 'plus' : 'minus'} title={r.why}><b>{r.s}</b><span>{r.text}</span></li>)}
    </ul></Section>}
    {detail?.quotes?.length ? <Section title="From the description">
      <div className="lc-tags">{detail.quotes.map((q) => <span key={q.signal} className="chip">{q.label}</span>)}</div>
      <p className="lp-quotes">{detail.quotes.map((q) => `“${q.quote}”`).join(' · ')}</p>
      <a className="lc-small" href={c.url} target="_blank" rel="noopener noreferrer">Full description on {publisher} ↗</a>
    </Section> : null}
    <p className="muted lc-small">
      {c.date ? `Listed ${shortDate(c.date)}` : 'List date not given'}
      {c.firstSeen ? ` · first seen ${shortDate(c.firstSeen)}` : ''}
      {c.lastSeen ? ` · last seen ${shortDate(c.lastSeen)}${daysSince(c.lastSeen) > 14 ? ' — may be off the market' : ''}` : ''}
    </p>
  </>
}

function Photos({ c, detail, photoCity, crops, open, publisher }: { c: Card; detail: Detail | null; photoCity: boolean; crops: Crop[]; open: (i: number) => void; publisher: string }) {
  if (!photoCity) return <p className="muted">Photo analysis: Philadelphia only for now.</p>
  const p = detail?.photo
  if (!c.photo) return <p className="muted">Photos not analysed yet.</p>
  if (!p) return <p className="muted">Loading…</p>
  const credit = p.credit ?? publisher
  let n = 0
  return <>
    <p className="lp-lead">{c.photo.findings ? `${c.photo.findings} ${c.photo.findings === 1 ? 'defect' : 'defects'} noted` : 'No defects seen'}
      <span className="muted lc-small"> · AI model, can be wrong{c.photo.findings ? '' : '; photos can’t rule problems out'}</span></p>
    {p.findings.map((f, i) => <Section key={i}>
      <div className="lp-find"><b>{f.label}{f.lowConfidence && <> <WarnMark /></>}</b><span className="muted">{f.room}{f.rooms > 1 ? ` · ${f.rooms} rooms` : ''}</span></div>
      {f.evidence && <p className="lc-small lp-ev">{f.evidence}</p>}
      {f.crops.length > 0 && <div className="lp-crops">{f.crops.map((src, i) => {
        const at = n++
        return <button key={src} className="lp-crop" onClick={() => open(at)}
          aria-label={`Enlarge: ${f.label}${f.lowConfidence ? ` (${LOW_MSG.toLowerCase()})` : ''}`}>
          <CropImage src={src} alt={`${f.label}, detected in a listing photo`} mark={f.marks?.[i]} point={f.points?.[i]} low={f.lowConfidence}
            caption={f.caption} cover /></button>
      })}</div>}
    </Section>)}
    {(p.missing.length > 0 || p.labelled.length > 0 || p.possiblyEdited) && <Section>
      {p.missing.length > 0 && <p className="lc-small">Not shown: {p.missing.join(', ')}</p>}
      {p.labelled.length > 0 && <p className="lc-small">Virtually staged (labelled: “{p.labelled[0]}”)</p>}
      {!p.labelled.length && p.possiblyEdited && <p className="lc-small muted">Possibly edited (low-confidence impression)</p>}
    </Section>}
    {crops.length > 0 && <p className="muted lc-small">Crops of {credit} photos (tap to enlarge) · <a href={p.creditUrl ?? c.url} target="_blank" rel="noopener noreferrer">all photos on {credit} ↗</a></p>}
  </>
}

function Price({ c, detail, city }: { c: Card; detail: Detail | null; city: City }) {
  const e = c.price ? c.estimate : undefined
  const ppsf = c.facts.sqft && c.price ? c.price / c.facts.sqft : null
  return <>
    <Section title="Estimate">{e ? <Breakdown c={c} e={e} city={city} max={8} /> : <p className="muted">No estimate — {lower(c.noEstimate ?? 'not enough to compare')}.</p>}</Section>
    {ppsf && c.sold?.ppsf12 && e?.kind !== 'ppsf' && <Section title="$/sq ft">
      <Pair rows={[{ label: 'Asking', value: ppsf, text: `$${Math.round(ppsf)}`, asking: true },
        { label: 'Sold here', value: c.sold.ppsf12, text: `$${c.sold.ppsf12}` }]} /></Section>}
    {(c.lastSale || detail?.record?.assessed) && <Section title="This address">
      {c.lastSale && <p className="lc-small">Last recorded sale <b>{usd(c.lastSale.price)}</b>, {monthYear(c.lastSale.date)}</p>}
      {detail?.record?.assessed && <p className="lc-small">City assessment <b>{usd(detail.record.assessed)}</b></p>}
    </Section>}
    {detail?.comps?.length ? <Section title="Recently sold nearby">
      <ul className="lp-comps">{detail.comps.map((s, i) => <li key={i}>
        <span>{s.address}</span><b>{usdCompact(s.price)}</b>
        <span className="muted">{monthYear(s.date)}{s.sqft ? ` · ${s.sqft.toLocaleString('en-US')} sq ft` : ''}{s.km != null ? ` · ${s.km < 1 ? `${Math.round(s.km * 1000)} m` : `${s.km} km`}` : ''}</span></li>)}</ul>
      <p className="muted lc-small">Recorded sales, not asking prices.</p>
    </Section> : null}
  </>
}

/** A thin bar for this area's value with a tick at the city figure (the Areas detail panel's pattern). */
function MiniBar({ v, c, lo, hi, tone }: { v: number | null; c: number | null; lo: number; hi: number; tone?: 'good' | 'bad' }) {
  if (v == null) return <span className="a-bar none" />
  const pos = (x: number) => Math.max(0, Math.min(1, (x - lo) / (hi - lo || 1))) * 100
  const base = lo < 0 ? pos(0) : 0, p = pos(v)
  return <span className={`a-bar ${tone ?? ''}`} aria-hidden="true">
    {lo < 0 && <i className="a-bar-zero" style={{ left: `${base}%` }} />}
    <i className="a-bar-fill" style={{ left: `${Math.min(base, p)}%`, width: `${Math.max(Math.abs(p - base), 2)}%` }} />
    {c != null && <i className="a-bar-city" style={{ left: `${pos(c)}%` }} />}
  </span>
}

function AreaTab({ c, area, areas, city, tier, ramp, cityGrowth }: { c: Card; area: Area | null; areas: Area[]; city: City; tier: Tier | undefined; ramp: Ramp; cityGrowth: number | null }) {
  const top = useMemo(() => {
    const q95 = (f: (a: Area) => number | null | undefined) => {
      const v = areas.map(f).filter((x): x is number => x != null).map(Math.abs).sort((a, b) => a - b)
      return v.length ? v[Math.floor(0.95 * (v.length - 1))] : 1
    }
    return { price: q95((a) => a.median12), g1: q95((a) => a.priceGrowth1 ?? a.medianYoY), yield: q95((a) => a.yield),
      violent: q95((a) => a.violentRate), property: q95((a) => a.propertyRate) }
  }, [areas])
  if (!c.area || !area) return <p className="muted">This home is not placed in an area.</p>
  const cx = city.context
  const level = (v: number | null | undefined, cv: number | null | undefined, t: number) => ({ v: v ?? null, c: cv ?? null, lo: 0, hi: Math.max(t, v ?? 0, cv ?? 0) * 1.05 })
  const g1 = area.priceGrowth1 ?? area.medianYoY
  const m = Math.max(top.g1, Math.abs(g1 ?? 0), Math.abs(cityGrowth ?? 0)) * 1.1
  const rows: { label: string; value: string; title?: string; bar?: ReturnType<typeof level> & { tone?: 'good' | 'bad' } }[] = [
    { label: 'Median sold', value: usd(area.median12), bar: level(area.median12, city.kpi.median12, top.price) },
    { label: 'Growth, 1 yr', value: g1 == null ? '–' : pct(g1), bar: { v: g1 ?? null, c: cityGrowth, lo: -m, hi: m, tone: g1 == null || g1 === 0 ? undefined : g1 > 0 ? 'good' : 'bad' } },
    { label: 'Rent', value: rentValue(area), title: area.rent == null ? undefined : rentLines(area, cx).join('\n') },
    { label: 'Rent yield', value: area.yield == null ? '–' : `${area.yield.toFixed(1)}%`, bar: level(area.yield, cx.yield, top.yield), title: yieldRange(area) ?? undefined },
    { label: 'Violent', value: area.violentRate?.toFixed(1) ?? '–', bar: level(area.violentRate, cx.violentRate, top.violent) },
    { label: 'Property', value: area.propertyRate?.toFixed(1) ?? '–', bar: level(area.propertyRate, cx.propertyRate, top.property) },
  ]
  return <>
    <div className="lp-area-head">
      {tier && <span className="f-tier mid" style={tierStyle(tier, ramp)}>{tier}</span>}
      <b>{c.area.name}</b>
      <a className="lc-small" href={`#/${city.id}/areas?area=${encodeURIComponent(c.area.key)}`}>Detailed View ›</a>
    </div>
    <p className="muted lc-small">{tier ? `Tier ${tier} under your tier weights.` : 'Too few sales for a tier.'}</p>
    <div className="a-rows">{rows.map((r) => <div key={r.label} className="a-row" title={r.title}>
      <span className="a-label">{r.label}</span><span className="a-val">{r.value}</span>{r.bar ? <MiniBar {...r.bar} /> : <span className="a-bar none" />}
    </div>)}</div>
    <p className="a-kv-note">Recorded sales; incidents per 1,000 residents. Bar: this area · line: city.</p>
  </>
}

function Lightbox({ crops, at, go, credit, href }: { crops: Crop[]; at: number; go: (i: number | null) => void; credit: string; href: string }) {
  const cur = crops[at]
  const n = crops.length
  const touch = useRef<number | null>(null)
  const closeRef = useRef<HTMLButtonElement>(null)
  const [size, setSize] = useState<number | null>(null)   // up to twice the stored crop's width (crops are at most 320 px)
  const [marks, setMarks] = useState(true)
  useEffect(() => setSize(null), [at])
  useEffect(() => {
    closeRef.current?.focus()
    const key = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { e.stopPropagation(); go(null) }
      if (e.key === 'ArrowRight') go((at + 1) % n)
      if (e.key === 'ArrowLeft') go((at - 1 + n) % n)
    }
    document.addEventListener('keydown', key, true)
    return () => document.removeEventListener('keydown', key, true)
  }, [at, n, go])
  return <div className="lp-lightbox" role="dialog" aria-modal="true" aria-label="Enlarged crop"
    onMouseDown={(e) => { if (e.target === e.currentTarget) go(null) }}
    onTouchStart={(e) => { touch.current = e.touches[0].clientX }}
    onTouchEnd={(e) => { const s = touch.current; touch.current = null; if (s == null) return; const dx = e.changedTouches[0].clientX - s; if (Math.abs(dx) > 40) go(((dx < 0 ? at + 1 : at - 1) + n) % n) }}>
    <figure>
      <CropImage key={cur.src} src={cur.src} alt={cur.caption} mark={cur.mark} point={cur.point} low={cur.low} caption={cur.short} marks={marks}
        style={size ? { width: size } : undefined} onSize={(w) => setSize(Math.min(2 * w, 640))} />
      <figcaption>
        <b>{cur.caption}</b>{cur.low && <> <WarnMark /></>}{cur.evidence ? <span> — {cur.evidence}</span> : null}
        <span className="muted"> · {at + 1} of {n} · crop of a {credit} photo, enlarged · <a href={href} target="_blank" rel="noopener noreferrer">listing ↗</a></span>
        {(cur.mark || cur.short) && <button type="button" className="lc-link lp-marks" onClick={() => setMarks(!marks)}
          aria-pressed={!marks}>{marks ? 'Hide marks' : 'Show marks'}</button>}
      </figcaption>
    </figure>
    {n > 1 && <button className="lp-nav prev" onClick={() => go((at - 1 + n) % n)} aria-label="Previous crop">‹</button>}
    {n > 1 && <button className="lp-nav next" onClick={() => go((at + 1) % n)} aria-label="Next crop">›</button>}
    <button ref={closeRef} className="lp-x" onClick={() => go(null)} aria-label="Close">✕</button>
  </div>
}
