/** One shared formatter: building one per call (toLocaleString) is slow in long tables. Same output. */
const NF = new Intl.NumberFormat('en-US')
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

export const num = (n: number | null | undefined) => (n == null ? '–' : NF.format(Math.round(n)))

export const usd = (n: number | null | undefined) => (n == null ? '–' : '$' + NF.format(Math.round(n)))

export function usdCompact(n: number | null | undefined): string {
  if (n == null) return '–'
  const a = Math.abs(n)
  if (a >= 1e6) return '$' + (n / 1e6).toFixed(a >= 1e7 ? 1 : 2).replace(/\.?0+$/, '') + 'M'
  if (a >= 1e4) return '$' + Math.round(n / 1e3) + 'K'
  return '$' + NF.format(Math.round(n))
}

export function compact(n: number | null | undefined): string {
  if (n == null) return '–'
  const a = Math.abs(n)
  if (a >= 1e6) return (n / 1e6).toFixed(1).replace(/\.0$/, '') + 'M'
  if (a >= 1e4) return Math.round(n / 1e3) + 'K'
  return NF.format(Math.round(n))
}

export const pct = (n: number | null | undefined, digits = 1) =>
  n == null ? '–' : (n > 0 ? '+' : n < 0 ? '−' : '') + Math.abs(n).toFixed(digits) + '%'

export const monthLabel = (m: string) => `${MONTHS[+m.slice(5, 7) - 1]} ${m.slice(0, 4)}`

export const dateLabel = (d: string) => `${MONTHS[+d.slice(5, 7) - 1]} ${+d.slice(8, 10)}, ${d.slice(0, 4)}`

export function timeAgo(iso: string): string {
  const mins = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60000))
  if (mins < 60) return `${mins} min ago`
  if (mins < 60 * 36) return `${Math.round(mins / 60)} hours ago`
  return `${Math.round(mins / 1440)} days ago`
}

export const dateTime = (iso: string) =>
  new Date(iso).toLocaleString('en-US', { month: 'short', day: 'numeric', year: 'numeric', hour: 'numeric', minute: '2-digit' })
