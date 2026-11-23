// Grading and ranking maths shared by Find areas and Areas.
// Kept free of runtime imports from other app files so `node src/lib/grades.test.mjs` can load it directly.
import { useMemo } from 'react'
import type { Theme } from './hooks'

export type Grade = 'A' | 'B' | 'C' | 'D' | 'E'
export type Tier = 'S' | 'A' | 'B' | 'C' | 'D'
export const TIERS: Tier[] = ['S', 'A', 'B', 'C', 'D']

/** Position on the --seq ramp (5 = strongest) for each tier and grade. */
export const TIER_SEQ: Record<Tier, number> = { S: 5, A: 4, B: 3, C: 2, D: 1 }
export const GRADE_SEQ: Record<Grade, number> = { A: 5, B: 4, C: 3, D: 2, E: 1 }

/** Percentile below which a factor counts as weak (grade D or E). Used by "must" floors and the S-tier guard. */
export const WEAK_BELOW = 40

/**
 * Percentile (0–100, exclusive) of each item within the list, from a value oriented so that higher is better.
 * Mid-rank method: tied values share their average rank, and percentile = (averageRank − 0.5) / n × 100,
 * so ties get the same percentile and nothing sits exactly at 0 or 100. Items with no value get none.
 * Rounded to one decimal.
 */
export function percentiles<T>(items: T[], key: (t: T) => string, value: (t: T) => number | null): Map<string, number> {
  const vals = items.map((t) => ({ k: key(t), v: value(t) })).filter((x): x is { k: string; v: number } => x.v != null && Number.isFinite(x.v)).sort((x, y) => x.v - y.v)
  const n = vals.length
  const out = new Map<string, number>()
  for (let i = 0; i < n;) {
    let j = i
    while (j + 1 < n && vals[j + 1].v === vals[i].v) j++
    const avgRank = (i + 1 + j + 1) / 2          // 1-based ranks i+1 … j+1
    const p = Math.round(((avgRank - 0.5) / n) * 1000) / 10
    for (let k = i; k <= j; k++) out.set(vals[k].k, p)
    i = j + 1
  }
  return out
}

/** Letter grade from a percentile: A top fifth, E bottom fifth. */
export const gradeOf = (p: number | null | undefined): Grade | null =>
  p == null ? null : p >= 80 ? 'A' : p >= 60 ? 'B' : p >= 40 ? 'C' : p >= 20 ? 'D' : 'E'

/**
 * Tier from a 0-based rank among n ranked items: S top 10%, A next 20%, B next 30%, C next 25%, D the rest.
 * S is never empty when there is at least one item.
 */
export function tierOf(rank: number, n: number): Tier {
  const ends = [0.1, 0.3, 0.6, 0.85].map((q) => Math.round(n * q))
  ends[0] = Math.max(ends[0], Math.min(1, n))
  for (let i = 1; i < ends.length; i++) ends[i] = Math.max(ends[i], ends[i - 1])
  const t = ends.findIndex((e) => rank < e)
  return TIERS[t < 0 ? 4 : t]
}

/**
 * Tiers for scores already sorted best first. Items with exactly the same score share the tier of
 * the first of them, so a tie that straddles a boundary goes to the higher tier.
 */
export function tiersForSorted(scores: number[]): Tier[] {
  const out: Tier[] = []
  let first = 0
  scores.forEach((s, i) => {
    if (i === 0 || s !== scores[i - 1]) first = i
    out.push(tierOf(first, scores.length))
  })
  return out
}

/**
 * Weighted geometric mean of percentiles: exp(Σ wᵢ·ln pᵢ / Σ wᵢ).
 * Unlike an arithmetic mean it is only partly compensatory: a weak percentile on a heavily weighted
 * factor pulls the result down more than a strong one elsewhere can lift it.
 */
export function geoMean(parts: { p: number; w: number }[]): number {
  let num = 0, den = 0
  for (const { p, w } of parts) {
    if (!w) continue
    num += w * Math.log(Math.max(p, 0.1))
    den += w
  }
  return den ? Math.exp(num / den) : 0
}

/** Even-weight blend used for the Areas page tier: geometric mean of the available percentiles. */
export const blendOf = (pcts: number[]): number => geoMean(pcts.map((p) => ({ p, w: 1 })))

// ---- Find areas ranking ----

export interface RankRow<F extends string> { key: string; pctl: Partial<Record<F, number>> }
export interface Ranked<F extends string> {
  key: string; score: number; rank: number; tier: Tier
  incomplete: F[]                       // weighted factors with no data, counted at the city median
  demoted: boolean                      // ranked in the S share but held to A by the guard
}
export interface RankResult<F extends string> {
  ranked: Ranked<F>[]
  hidden: { key: string; reasons: F[] }[]   // removed by "must" floors
}

/**
 * Score and tier a pool of areas.
 * - score: weighted geometric mean of percentiles; missing weighted factors count as the median (50)
 * - areas missing more than 40% of the total weight are left out
 * - `must` factors remove areas below WEAK_BELOW (or with no data) on that factor
 * - tiers by rank share with ties at a boundary going up; an area cannot be S if it is incomplete or
 *   weak on any factor weighted 4 or more (it is shown as A; S is not backfilled)
 */
export function rankPool<F extends string>(rows: RankRow<F>[], factors: F[], w: Record<F, number>, must: Iterable<F> = []): RankResult<F> {
  const musts = [...must]
  const wanted = factors.reduce((t, f) => t + (w[f] || 0), 0)
  const hidden: RankResult<F>['hidden'] = []
  const scored: { key: string; score: number; incomplete: F[]; heavyWeak: boolean }[] = []
  for (const r of rows) {
    const reasons = musts.filter((f) => r.pctl[f] == null || (r.pctl[f] as number) < WEAK_BELOW)
    if (reasons.length) { hidden.push({ key: r.key, reasons }); continue }
    if (!wanted) continue
    const incomplete: F[] = []
    let missingW = 0
    const parts: { p: number; w: number }[] = []
    for (const f of factors) {
      if (!w[f]) continue
      const p = r.pctl[f]
      if (p == null) { incomplete.push(f); missingW += w[f]; parts.push({ p: 50, w: w[f] }) }
      else parts.push({ p, w: w[f] })
    }
    if (missingW > wanted * 0.4) continue      // too little data to rank fairly
    const heavyWeak = factors.some((f) => w[f] >= 4 && r.pctl[f] != null && (r.pctl[f] as number) < WEAK_BELOW)
    scored.push({ key: r.key, score: Math.round(geoMean(parts)), incomplete, heavyWeak })
  }
  scored.sort((a, b) => b.score - a.score || a.key.localeCompare(b.key))
  const tiers = tiersForSorted(scored.map((s) => s.score))
  const ranked = scored.map((s, i): Ranked<F> => {
    const blocked = tiers[i] === 'S' && (s.heavyWeak || s.incomplete.length > 0)
    return { key: s.key, score: s.score, rank: i + 1, tier: blocked ? 'A' : tiers[i], incomplete: s.incomplete, demoted: blocked }
  })
  return { ranked, hidden }
}

/** Small seeded PRNG (mulberry32) so the robustness readout is stable between renders. */
export function prng(seed: number): () => number {
  let a = seed >>> 0
  return () => {
    a = (a + 0x6d2b79f5) >>> 0
    let t = a
    t = Math.imul(t ^ (t >>> 15), t | 1)
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61)
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

export interface Robust { top10: number; lo: number; hi: number; runs: number }

/**
 * Cheap sensitivity analysis: re-rank `runs` times with each non-zero weight nudged by −1, 0 or +1
 * (clamped to 0–5, at least one weight kept above 0). Returns, per area, the share of runs in which it
 * made the top 10 and its 5th–95th percentile rank.
 */
export function robustness<F extends string>(rows: RankRow<F>[], factors: F[], w: Record<F, number>, must: Iterable<F> = [], runs = 200, seed = 1234): Map<string, Robust> {
  const rnd = prng(seed)
  const musts = [...must]
  const seen = new Map<string, number[]>()
  for (let r = 0; r < runs; r++) {
    const ww = { ...w }
    for (const f of factors) if (w[f] > 0) ww[f] = Math.min(5, Math.max(0, w[f] + Math.floor(rnd() * 3) - 1))
    if (!factors.some((f) => ww[f] > 0)) { const f = factors.find((x) => w[x] > 0); if (f) ww[f] = 1 }
    rankPool(rows, factors, ww, musts).ranked.forEach((x) => { const a = seen.get(x.key); if (a) a.push(x.rank); else seen.set(x.key, [x.rank]) })
  }
  const out = new Map<string, Robust>()
  seen.forEach((ranks, key) => {
    ranks.sort((a, b) => a - b)
    const q = (p: number) => ranks[Math.min(ranks.length - 1, Math.max(0, Math.round(p * (ranks.length - 1))))]
    out.set(key, { top10: ranks.filter((x) => x <= 10).length / runs, lo: q(0.05), hi: q(0.95), runs })
  })
  return out
}

// ---- colours ----

const cssVar = (name: string) => getComputedStyle(document.documentElement).getPropertyValue(name).trim()

/** Pick the more readable ink (page background or text colour) for a fill colour. */
function inkFor(fill: string): string {
  const lum = (c: string) => {
    const m = c.match(/^#([0-9a-f]{6})$/i)
    if (!m) return null
    const v = [0, 2, 4].map((i) => parseInt(m[1].slice(i, i + 2), 16) / 255).map((x) => (x <= 0.03928 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4))
    return 0.2126 * v[0] + 0.7152 * v[1] + 0.0722 * v[2]
  }
  const f = lum(fill), b = lum(cssVar('--bg')), t = lum(cssVar('--text'))
  if (f == null || b == null || t == null) return 'var(--text)'
  const ratio = (x: number, y: number) => (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05)
  return ratio(f, b) >= ratio(f, t) ? 'var(--bg)' : 'var(--text)'
}

export type Ramp = Record<number, { bg: string; ink: string }>

/** Fill and ink for each step of the --seq ramp, re-read when the theme changes. */
export function useRamp(theme: Theme): Ramp {
  return useMemo(() => {
    const out: Ramp = {}
    for (let i = 1; i <= 5; i++) { const c = cssVar(`--seq-${i}`); out[i] = { bg: c || `var(--seq-${i})`, ink: inkFor(c) } }
    return out
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [theme])
}

/** Inline style for a tier tile or grade square. Grade E is styled by the `.e` class instead. */
export const tierStyle = (t: Tier, ramp: Ramp) => ({ background: ramp[TIER_SEQ[t]].bg, color: ramp[TIER_SEQ[t]].ink })
export const gradeStyle = (g: Grade, ramp: Ramp) => (g === 'E' ? undefined : { background: ramp[GRADE_SEQ[g]].bg, color: ramp[GRADE_SEQ[g]].ink })
