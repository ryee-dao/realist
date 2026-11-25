// The thin comparison bar used by the Neighborhoods panel and the listing cards' Area tab.
import '../styles/scales.css'   // the red of the comparison colours lives there
import '../styles/areas.css'

/** A thin bullet bar: the area's value as a bar (from zero, or from the middle for changes) and a tick at the city figure. */
export type Tip = { area: string; city: string | null; better?: 'up' | 'down' }

/** How this area's figure stands against the city's, on the bar's own scale: 2 clearly better … -2 clearly worse, 0 about level. */
function standingVs(v: number, c: number | null, span: number, better?: 'up' | 'down') {
  if (c == null || !better || !span) return 0
  const d = ((v - c) / span) * (better === 'up' ? 1 : -1)
  return Math.abs(d) < 0.02 ? 0 : (Math.abs(d) < 0.12 ? 1 : 2) * Math.sign(d)
}

export function MiniBar({ v, c, lo, hi, tone, tip, quiet }: { v: number | null; c: number | null; lo: number; hi: number; tone?: 'good' | 'bad'; tip?: Tip; quiet?: boolean }) {
  if (v == null) return <span className="a-bar" />
  const pos = (x: number) => Math.max(0, Math.min(1, (x - lo) / (hi - lo || 1))) * 100
  const base = lo < 0 ? pos(0) : 0
  const p = pos(v)
  const s = standingVs(v, c, hi - lo, tip?.better)
  const cls = s ? `s${s > 0 ? 'p' : 'n'}${Math.abs(s)}` : ''
  // graded rows take their colour from the standing against the city; `tone` is for bars with no city figure to stand against
  const colour = tip?.better && c != null ? cls : tone ?? (tip && !tip.better ? 'plain' : '')   // plain: a size, not something graded
  return (
    // title="" stops the row's own long hover text from showing over the bar; the bar has its short one
    <span className={`a-bar ${colour} ${quiet ? 'quiet' : ''}`} aria-hidden="true" title="">
      {lo < 0 && <i className="a-bar-zero" style={{ left: `${base}%` }} />}
      <i className="a-bar-fill" style={{ left: `${Math.min(base, p)}%`, width: `${Math.max(Math.abs(p - base), 2)}%` }} />
      {c != null && <i className="a-bar-city" style={{ left: `${pos(c)}%` }} />}
      {tip && <span className="a-tip"><b className={cls}>Area {tip.area}</b>{tip.city != null && <> · City {tip.city}</>}</span>}
    </span>
  )
}
