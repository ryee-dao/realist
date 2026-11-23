import { Run, SourceReport } from './data'

/** What a city's sales sources read and kept. A source unchanged today reports its last real pull. */
export function cityFunnel(run: Run, cityId: string) {
  const srcs = (run.cities[cityId]?.sources ?? []).filter((s) => !s.context).map((s) => (s.skipped && s.last ? { ...s, ...s.last } : s))
  const read = srcs.reduce((t, s) => t + s.fetched, 0)
  const kept = srcs.reduce((t, s) => t + s.kept, 0)
  const reasons: Record<string, number> = {}
  for (const s of srcs) for (const [k, v] of Object.entries(s.rejected)) reasons[k] = (reasons[k] ?? 0) + v
  const ranked = Object.entries(reasons).sort((a, b) => b[1] - a[1])
  const asOf = srcs.find((s) => s.skipped && s.last)?.last?.date
  return { read, kept, ranked, asOf }
}

/** Seconds until the next scheduled daily run (09:17 UTC). */
export function secondsToNextRun(now = new Date()): number {
  const next = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate(), 9, 17, 0))
  if (next.getTime() <= now.getTime()) next.setUTCDate(next.getUTCDate() + 1)
  return Math.round((next.getTime() - now.getTime()) / 1000)
}

/** hh:mm:ss for a number of seconds. */
export function hms(s: number): string {
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${pad(Math.floor(s / 3600))}:${pad(Math.floor((s % 3600) / 60))}:${pad(s % 60)}`
}

/** How a source fared in the last run. */
export function sourceState(s: SourceReport): 'pulled' | 'unchanged' | 'failed' {
  return !s.skipped ? 'pulled' : s.skipped.startsWith('failed') ? 'failed' : 'unchanged'
}

/** Stage counts for one city's part of a run: requests, read/kept (via cityFunnel), checks passed. */
export function cityStages(run: Run, cityId: string) {
  const f = cityFunnel(run, cityId)
  const requests = (run.cities[cityId]?.sources ?? []).reduce((t, s) => t + s.requests, 0)
  const checks = run.checks.filter((c) => c.city === cityId)
  return { ...f, requests, checks, passed: checks.filter((c) => c.status === 'pass').length }
}
