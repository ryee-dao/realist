/** Clean axis ticks covering [min, max]. */
export function niceTicks(min: number, max: number, target = 5): number[] {
  if (!isFinite(min) || !isFinite(max)) return [0, 1]
  if (min === max) { min = min * 0.9; max = max * 1.1 || 1 }
  const raw = (max - min) / target
  const pow = Math.pow(10, Math.floor(Math.log10(raw)))
  const step = [1, 2, 2.5, 5, 10].map((m) => m * pow).find((s) => s >= raw) ?? 10 * pow
  const lo = Math.floor(min / step) * step
  const hi = Math.ceil(max / step) * step
  const ticks: number[] = []
  for (let v = lo; v <= hi + step / 2; v += step) ticks.push(Math.round(v * 1e6) / 1e6)
  return ticks
}

export interface Tick { index: number; label: string }

/** One tick per January, thinned so labels never collide. */
export function yearTicks(months: string[], width: number): Tick[] {
  const all = months.map((m, index) => ({ index, label: m.slice(0, 4), jan: m.endsWith('-01') })).filter((t) => t.jan)
  const every = Math.max(1, Math.ceil(all.length / Math.max(2, Math.floor(width / 56))))
  return all.filter((_, i) => (all.length - 1 - i) % every === 0)
}

export function linePath(points: ([number, number] | null)[]): string {
  let d = ''
  let pen = false
  for (const p of points) {
    if (!p) { pen = false; continue }
    d += `${pen ? 'L' : 'M'}${p[0].toFixed(1)},${p[1].toFixed(1)}`
    pen = true
  }
  return d
}
