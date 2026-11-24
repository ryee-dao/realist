import { useState } from 'react'
import { useWidth } from '../lib/hooks'
import { niceTicks, Tick } from './scale'

interface Props {
  labels: string[]                 // tooltip title per column
  values: number[]
  ticks: (width: number) => Tick[]
  /** 'center' puts the tick under the column; 'edge' puts it on the column's left edge (histograms). */
  tickAlign?: 'center' | 'edge'
  format: (n: number) => string
  unit: string                     // what one column counts, e.g. "homes sold"
  tipFormat?: (n: number) => string // tooltip value; defaults to a plain number
  color?: string
  height?: number
}

const M = { top: 12, right: 16, bottom: 26, left: 52 }

export default function ColumnChart({ labels, values, ticks, tickAlign = 'center', format, unit, tipFormat, color = 'var(--series-1)', height = 220 }: Props) {
  const [ref, width] = useWidth<HTMLDivElement>()
  const [hover, setHover] = useState<number | null>(null)
  const n = values.length
  const w = Math.max(10, width - M.left - M.right)
  const h = height - M.top - M.bottom
  const yt = niceTicks(0, Math.max(1, ...values), 4)
  const top = yt[yt.length - 1]
  const band = w / Math.max(1, n)
  const bar = Math.max(1, Math.min(24, band - 2))
  const y = (v: number) => M.top + h - (v / top) * h
  const cx = (i: number) => M.left + band * (i + 0.5)

  const onMove = (e: React.PointerEvent) => {
    const box = (e.currentTarget as SVGElement).getBoundingClientRect()
    const i = Math.floor((e.clientX - box.left - M.left) / band)
    setHover(i >= 0 && i < n ? i : null)
  }
  const tipLeft = hover == null ? 0 : cx(hover)
  const flip = tipLeft > width * 0.6

  return (
    <div className="chart" ref={ref} style={{ minHeight: height }}>
      {width > 0 && (
        <svg width={width} height={height} role="img" onPointerMove={onMove} onPointerLeave={() => setHover(null)}>
          {yt.map((t) => (
            <g key={t}>
              <line className={t === 0 ? 'baseline' : 'grid'} x1={M.left} x2={M.left + w} y1={y(t)} y2={y(t)} />
              <text className="axis" x={M.left - 8} y={y(t)} dy="0.32em" textAnchor="end">{format(t)}</text>
            </g>
          ))}
          {ticks(w).map((t) => (
            <text key={t.index} className="axis" x={tickAlign === 'edge' ? M.left + band * t.index : cx(t.index)} y={height - 8} textAnchor="middle">{t.label}</text>
          ))}
          {values.map((v, i) => {
            if (v <= 0) return null
            const bh = Math.max(1, M.top + h - y(v))
            const r = Math.min(4, bar / 2, bh)
            const x0 = cx(i) - bar / 2
            const yTop = M.top + h - bh
            return (
              <path key={i} fill={color} opacity={hover == null || hover === i ? 1 : 0.55}
                d={`M${x0},${M.top + h}V${yTop + r}Q${x0},${yTop} ${x0 + r},${yTop}H${x0 + bar - r}Q${x0 + bar},${yTop} ${x0 + bar},${yTop + r}V${M.top + h}Z`} />
            )
          })}
        </svg>
      )}
      {hover != null && (
        <div className="tooltip" style={flip ? { right: width - tipLeft + 12, top: M.top } : { left: tipLeft + 12, top: M.top }}>
          <div className="tooltip-title">{labels[hover]}</div>
          <div className="tooltip-row"><span>{unit}</span><b>{tipFormat ? tipFormat(values[hover]) : values[hover].toLocaleString('en-US')}</b></div>
        </div>
      )}
    </div>
  )
}
