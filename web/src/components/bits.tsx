import { ReactNode } from 'react'
import { pct } from '../lib/format'

/** Phones stack the detail panel away from the map and table that select it: bring it into view after a pick.
 *  'nearest' leaves it alone when it is already on screen. */
/** The yellow "!" marks: remember where the mark sits so its tip can open within the screen on phones (styles.css). */
export function placeTip(e: { currentTarget: HTMLElement }) {
  e.currentTarget.style.setProperty('--tip-x', `${Math.round(e.currentTarget.getBoundingClientRect().left)}px`)
}

export function revealOnPhone(el: Element | null | undefined) {
  if (!el || window.innerWidth >= 900) return
  const smooth = !window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
  requestAnimationFrame(() => el.scrollIntoView({ block: 'nearest', behavior: smooth ? 'smooth' : 'auto' }))
}

/** A change figure. Rising prices are not "good" or "bad", so direction is shown by arrow, not colour. */
export function Delta({ value, suffix, points, short }: {
  value: number | null | undefined; suffix?: string
  points?: boolean   // the value is a change in percentage points, not a percent change
  short?: boolean    // show a dash rather than words when there is no comparison
}) {
  if (value == null) return <span className="delta muted">{short ? '–' : 'no comparison yet'}</span>
  const arrow = value > 0.05 ? '▲' : value < -0.05 ? '▼' : '■'
  const text = points ? `${value > 0 ? '+' : value < 0 ? '−' : ''}${Math.abs(value).toFixed(1)} pt` : pct(value)
  return (
    <span className="delta">
      <span className="arrow" aria-hidden="true">{arrow}</span> {text}{suffix ? ` ${suffix}` : ''}
    </span>
  )
}

export function StatTile({ label, value, children, hero }: { label: string; value: string; children?: ReactNode; hero?: boolean }) {
  return (
    <div className={`tile ${hero ? 'hero' : ''}`}>
      <div className="tile-label">{label}</div>
      <div className="tile-value">{value}</div>
      <div className="tile-foot">{children}</div>
    </div>
  )
}

export function Segmented<T extends string>({ options, value, onChange, label }: {
  options: { id: T; label: string }[]; value: T; onChange: (v: T) => void; label: string
}) {
  return (
    <div className="segmented" role="group" aria-label={label}>
      {options.map((o) => (
        <button key={o.id} className={o.id === value ? 'on' : ''} aria-pressed={o.id === value} onClick={() => onChange(o.id)}>{o.label}</button>
      ))}
    </div>
  )
}

export const TYPE_COLOR: Record<string, string> = {
  'All homes': 'var(--series-1)',
  'Single family': 'var(--type-1)',
  Condo: 'var(--type-2)',
  'Co-op': 'var(--type-3)',
  'Multi-family': 'var(--type-4)',
}

export function SortHead<K extends string>({ id, label, sort, onSort, num }: {
  id: K; label: string; sort: { key: K; dir: 1 | -1 }; onSort: (k: K) => void; num?: boolean
}) {
  const on = sort.key === id
  return (
    <th className={num ? 'num' : ''} aria-sort={on ? (sort.dir === 1 ? 'ascending' : 'descending') : 'none'}>
      <button className="sort" onClick={() => onSort(id)}>
        {label}<span className="sort-mark">{on ? (sort.dir === 1 ? '↑' : '↓') : ''}</span>
      </button>
    </th>
  )
}
