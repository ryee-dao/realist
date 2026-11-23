import { linePath } from './scale'

export default function Sparkline({ values, width = 96, height = 28 }: { values: (number | null)[]; width?: number; height?: number }) {
  const present = values.filter((v): v is number => v != null)
  if (present.length < 2) return <span className="muted">–</span>
  const lo = Math.min(...present)
  const hi = Math.max(...present)
  const pad = 4
  const x = (i: number) => pad + (i / (values.length - 1)) * (width - pad * 2)
  const y = (v: number) => height - pad - ((v - lo) / (hi - lo || 1)) * (height - pad * 2)
  let last = values.length - 1
  while (values[last] == null) last--
  return (
    <svg width={width} height={height} aria-hidden="true" className="spark">
      <path d={linePath(values.map((v, i) => (v == null ? null : [x(i), y(v)])))} fill="none" stroke="var(--spark)" strokeWidth={1.5} strokeLinejoin="round" />
      <circle cx={x(last)} cy={y(values[last] as number)} r={2.5} fill="var(--series-1)" />
    </svg>
  )
}
