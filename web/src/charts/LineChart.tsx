import { useState } from 'react'
import { useWidth } from '../lib/hooks'
import { linePath, niceTicks, Tick } from './scale'

export interface LineSeries {
  name: string
  color: string
  values: (number | null)[]
  /** Optional shaded range around the line (e.g. the middle 50% of sales). */
  band?: { lo: (number | null)[]; hi: (number | null)[]; label: string }
}

interface Props {
  labels: string[]                       // one per x position, shown in the tooltip
  ticks: (width: number) => Tick[]
  series: LineSeries[]
  format: (n: number) => string
  axisFormat?: (n: number) => string
  height?: number
}

const M = { top: 12, right: 16, bottom: 26, left: 52 }

export default function LineChart({ labels, ticks, series, format, axisFormat = format, height = 280 }: Props) {
  const [ref, width] = useWidth<HTMLDivElement>()
  const [hover, setHover] = useState<number | null>(null)
  const n = labels.length
  const direct = series.length > 1 && series.length <= 4
  const right = direct ? 108 : M.right
  const w = Math.max(10, width - M.left - right)
  const h = height - M.top - M.bottom

  const all: number[] = []
  for (const s of series) {
    for (const v of s.values) if (v != null) all.push(v)
    if (s.band) for (const v of [...s.band.lo, ...s.band.hi]) if (v != null) all.push(v)
  }
  const yt = niceTicks(Math.min(...all), Math.max(...all), 4)
  const [y0, y1] = [yt[0], yt[yt.length - 1]]
  const x = (i: number) => M.left + (n <= 1 ? w / 2 : (i / (n - 1)) * w)
  const y = (v: number) => M.top + h - ((v - y0) / (y1 - y0 || 1)) * h
  const pts = (vals: (number | null)[]) => vals.map((v, i) => (v == null ? null : ([x(i), y(v)] as [number, number])))

  // End labels: keep them only if they would not overlap each other.
  const ends = series.map((s) => {
    let i = s.values.length - 1
    while (i >= 0 && s.values[i] == null) i--
    return i < 0 ? null : { s, i, y: y(s.values[i] as number) }
  }).filter(Boolean) as { s: LineSeries; i: number; y: number }[]
  const sorted = [...ends].sort((a, b) => a.y - b.y)
  const labelsFit = direct && sorted.every((e, k) => k === 0 || e.y - sorted[k - 1].y >= 15)

  const onMove = (e: React.PointerEvent) => {
    const box = (e.currentTarget as SVGElement).getBoundingClientRect()
    const i = Math.round(((e.clientX - box.left - M.left) / w) * (n - 1))
    setHover(Math.max(0, Math.min(n - 1, i)))
  }
  const tipLeft = hover == null ? 0 : x(hover)
  const flip = tipLeft > width * 0.6

  return (
    <div className="chart" ref={ref} style={{ minHeight: height }}>
      {series.length > 1 && (
        <div className="legend">
          {series.map((s) => (
            <span key={s.name}><i className="key-line" style={{ background: s.color }} />{s.name}</span>
          ))}
        </div>
      )}
      {width > 0 && all.length > 0 && (
        <svg width={width} height={height} role="img" onPointerMove={onMove} onPointerLeave={() => setHover(null)}>
          {yt.map((t) => (
            <g key={t}>
              <line className="grid" x1={M.left} x2={M.left + w} y1={y(t)} y2={y(t)} />
              <text className="axis" x={M.left - 8} y={y(t)} dy="0.32em" textAnchor="end">{axisFormat(t)}</text>
            </g>
          ))}
          {ticks(w).map((t) => (
            <text key={t.index} className="axis" x={x(t.index)} y={height - 8} textAnchor="middle">{t.label}</text>
          ))}
          {series.map((s) => s.band && (
            <path key={s.name + 'band'} fill={s.color} opacity={0.12} d={bandPath(pts(s.band.lo), pts(s.band.hi))} />
          ))}
          {series.map((s) => (
            <path key={s.name} d={linePath(pts(s.values))} fill="none" stroke={s.color} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
          ))}
          {ends.map(({ s, i, y: ey }) => (
            <g key={s.name}>
              <circle cx={x(i)} cy={ey} r={4} fill={s.color} stroke="var(--surface)" strokeWidth={2} />
              {labelsFit && <text className="end-label" x={x(i) + 9} y={ey} dy="0.32em">{s.name}</text>}
            </g>
          ))}
          {hover != null && (
            <g pointerEvents="none">
              <line className="crosshair" x1={x(hover)} x2={x(hover)} y1={M.top} y2={M.top + h} />
              {series.map((s) => s.values[hover] != null && (
                <circle key={s.name} cx={x(hover)} cy={y(s.values[hover] as number)} r={4.5} fill={s.color} stroke="var(--surface)" strokeWidth={2} />
              ))}
            </g>
          )}
        </svg>
      )}
      {hover != null && (
        <div className="tooltip" style={flip ? { right: width - tipLeft + 12, top: M.top } : { left: tipLeft + 12, top: M.top }}>
          <div className="tooltip-title">{labels[hover]}</div>
          {series.map((s) => (
            <div key={s.name} className="tooltip-row">
              <span><i className="key-line" style={{ background: s.color }} />{s.name}</span>
              <b>{s.values[hover] == null ? 'too few sales' : format(s.values[hover] as number)}</b>
            </div>
          ))}
          {series.map((s) => s.band && s.band.lo[hover] != null && (
            <div key={s.name + 'b'} className="tooltip-row muted">
              <span>{s.band.label}</span>
              <b>{format(s.band.lo[hover] as number)} – {format(s.band.hi[hover] as number)}</b>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function bandPath(lo: ([number, number] | null)[], hi: ([number, number] | null)[]): string {
  // Draw each unbroken run as its own closed shape.
  let d = ''
  let run: number[] = []
  const close = () => {
    if (run.length > 1) {
      d += run.map((i, k) => `${k ? 'L' : 'M'}${hi[i]![0].toFixed(1)},${hi[i]![1].toFixed(1)}`).join('')
      d += [...run].reverse().map((i) => `L${lo[i]![0].toFixed(1)},${lo[i]![1].toFixed(1)}`).join('') + 'Z'
    }
    run = []
  }
  lo.forEach((p, i) => (p && hi[i] ? run.push(i) : close()))
  close()
  return d
}
