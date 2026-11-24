// Run with: node src/lib/grades.test.mjs   (Node 22.18+ strips the TypeScript types in grades.ts)
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { activityPercentiles, blendOf, geoMean, gradeOf, LISTED_SHARE, percentiles, rankPool, robustness, tiersForSorted } from './grades.ts'

const F = ['yield', 'growth1', 'growth5', 'price', 'activity', 'crime']

// 1. ties share a percentile, nothing hits 0 or 100
{
  const items = [1, 2, 2, 3].map((v, i) => ({ k: `a${i}`, v }))
  const p = percentiles(items, (x) => x.k, (x) => x.v)
  assert.equal(p.get('a1'), p.get('a2'))
  assert.equal(p.get('a0'), 12.5)
  assert.equal(p.get('a1'), 50)
  assert.equal(p.get('a3'), 87.5)
  const flat = percentiles([{ k: 'x', v: 5 }, { k: 'y', v: 5 }], (x) => x.k, (x) => x.v)
  assert.equal(flat.get('x'), 50)
  // order of input does not matter for ties
  const rev = percentiles([...items].reverse(), (x) => x.k, (x) => x.v)
  assert.deepEqual([...rev.entries()].sort(), [...p.entries()].sort())
  console.log('ties:', Object.fromEntries(p))
}

// 2. the X / Y example: arithmetic prefers X, geometric prefers Y, "must" on incidents hides X
{
  const w = { yield: 5, growth1: 1, growth5: 1, price: 2, activity: 2, crime: 5 }
  const X = { yield: 95, growth1: 70, growth5: 70, price: 90, activity: 70, crime: 30 }
  const Y = Object.fromEntries(F.map((f) => [f, 65]))
  const arith = (p) => F.reduce((t, f) => t + p[f] * w[f], 0) / F.reduce((t, f) => t + w[f], 0)
  const geo = (p) => geoMean(F.map((f) => ({ p: p[f], w: w[f] })))
  console.log(`X/Y arithmetic ${arith(X).toFixed(1)} vs ${arith(Y).toFixed(1)}; geometric ${geo(X).toFixed(1)} vs ${geo(Y).toFixed(1)}`)
  assert.ok(arith(X) > arith(Y))
  assert.ok(geo(X) < geo(Y))
  const rows = [{ key: 'X', pctl: X }, { key: 'Y', pctl: Y }]
  const r = rankPool(rows, F, w)
  assert.equal(r.ranked[0].key, 'Y')
  const m = rankPool(rows, F, w, ['crime'])
  assert.deepEqual(m.ranked.map((x) => x.key), ['Y'])
  assert.deepEqual(m.hidden, [{ key: 'X', reasons: ['crime'] }])
}

// 3. S-tier guard: top-scoring area weak on a factor weighted 4+ is held to A, S is not backfilled
{
  const w = { yield: 5, growth1: 0, growth5: 0, price: 0, activity: 0, crime: 4 }
  const rows = Array.from({ length: 10 }, (_, i) => ({ key: `r${i}`, pctl: { yield: 60 - i * 5, crime: 60 } }))
  rows[0].pctl = { yield: 99.9, crime: 39 }     // best score, but D on a weight-4 factor
  const r = rankPool(rows, F, w)
  const top = r.ranked[0]
  assert.equal(top.key, 'r0')
  assert.equal(top.tier, 'A')
  assert.ok(top.demoted)
  assert.equal(r.ranked.filter((x) => x.tier === 'S').length, 0)
  // missing data: counted at 50, marked incomplete, never S
  const rows2 = [{ key: 'm', pctl: { yield: 99 } }, ...Array.from({ length: 9 }, (_, i) => ({ key: `n${i}`, pctl: { yield: 50 - i, crime: 50 } }))]
  const r2 = rankPool(rows2, F, { ...w, crime: 1 })
  const m = r2.ranked.find((x) => x.key === 'm')
  assert.deepEqual(m.incomplete, ['crime'])
  assert.notEqual(m.tier, 'S')
  // > 40% of weight missing: left out
  const r3 = rankPool([{ key: 'z', pctl: { yield: 99 } }], F, { ...w, crime: 5 })
  assert.equal(r3.ranked.length, 0)
}

// 4. boundary tie: equal scores straddling the S/A cut both go to S
{
  const t = tiersForSorted([90, 80, 80, 70, 60, 50, 40, 30, 20, 10])   // S share of 10 = 1 item
  assert.deepEqual(t.slice(0, 3), ['S', 'A', 'A'])
  const t2 = tiersForSorted([90, 90, 80, 70, 60, 50, 40, 30, 20, 10])
  assert.deepEqual(t2.slice(0, 3), ['S', 'S', 'A'])
}

// 5. grades and even blend
assert.equal(gradeOf(80), 'A'); assert.equal(gradeOf(39.9), 'D'); assert.equal(gradeOf(19), 'E')
assert.ok(Math.abs(blendOf([50, 50]) - 50) < 1e-9)

// 6. real data: Philadelphia, incidents weight 5: no S-tier area graded D or E on incidents
{
  const raw = {
    yield: (a) => a.yield, growth1: (a) => a.priceGrowth1 ?? a.medianYoY, growth5: (a) => a.priceGrowth5 ?? a.change5y,
    price: (a) => (a.median12 == null ? null : -a.median12), activity: (a) => a.sales12,
    crime: (a) => (a.violentRate == null || a.propertyRate == null ? null : -(a.violentRate * 3 + a.propertyRate)),
  }
  const data = JSON.parse(readFileSync(new URL('../../public/data/philadelphia/areas.json', import.meta.url)))
  const pool = data.areas.filter((a) => a.median12 != null && a.sales12 >= 30)
  const pc = Object.fromEntries(F.map((f) => [f, percentiles(pool, (a) => a.key, raw[f])]))
  const rows = pool.map((a) => ({ key: a.key, pctl: Object.fromEntries(F.filter((f) => pc[f].has(a.key)).map((f) => [f, pc[f].get(a.key)])) }))
  const w = { yield: 5, growth1: 1, growth5: 1, price: 1, activity: 2, crime: 5 }
  const r = rankPool(rows, F, w)
  const byKey = new Map(rows.map((x) => [x.key, x]))
  const name = new Map(pool.map((a) => [a.key, a.name]))
  const s = r.ranked.filter((x) => x.tier === 'S')
  console.log(`Philadelphia, w=5,1,1,1,2,5: ${r.ranked.length} ranked, S tier:`, s.map((x) => `${name.get(x.key)} ${x.score} incidents ${gradeOf(byKey.get(x.key).pctl.crime)}`).join('; '))
  console.log('  held from S by the guard:', r.ranked.filter((x) => x.demoted).map((x) => name.get(x.key)).join(', ') || 'none')
  for (const x of s) assert.ok(!['D', 'E'].includes(gradeOf(byKey.get(x.key).pctl.crime)))
  const m = rankPool(rows, F, w, ['crime'])
  console.log(`  with must on incidents: ${m.ranked.length} ranked, ${m.hidden.length} hidden`)
  const t0 = performance.now()
  const rb = robustness(rows, F, w)
  const top = r.ranked.slice(0, 3).map((x) => { const q = rb.get(x.key); return `${name.get(x.key)} #${x.rank}: ranks ${q.lo}–${q.hi}, top 10 in ${Math.round(q.top10 * 100)}%` })
  console.log(`  robustness (${Math.round(performance.now() - t0)} ms):`, top.join('; '))
  // stable between calls
  assert.deepEqual(robustness(rows, F, w).get(r.ranked[0].key), rb.get(r.ranked[0].key))
}

// 7. "Active market" composite: sales dominate, listings a minor part, no listings = sales alone
{
  assert.equal(LISTED_SHARE, 0.25)
  const items = [
    { k: 'a', sold: 100, active: 1, fresh: 1 },     // most sales, fewest listings
    { k: 'b', sold: 90, active: 50, fresh: 50 },
    { k: 'c', sold: 10, active: 99, fresh: 99 },    // fewest sales, most listings
    { k: 'd', sold: 50, active: null, fresh: null },// no listing figures
  ]
  const p = activityPercentiles(items, (x) => x.k, (x) => x.sold, [(x) => x.active, (x) => x.fresh])
  console.log('activity composite:', Object.fromEntries(p))
  // listings cannot lift the area with the fewest sales above the busiest ones
  assert.ok(p.get('c') < p.get('a') && p.get('c') < p.get('b'))
  // no listing figures: ranked on sales alone, still has a percentile (not incomplete)
  assert.ok(p.get('d') != null)
  // with a 3/4 sales weight, blend values: a 0.75*87.5+0.25*16.7, b 0.75*62.5+0.25*50, c 0.75*12.5+0.25*83.3, d 37.5
  const raw = { a: 0.75 * 87.5 + 0.25 * (100 / 6), b: 0.75 * 62.5 + 0.25 * 50, c: 0.75 * 12.5 + 0.25 * (500 / 6), d: 37.5 }
  const order = Object.keys(raw).sort((x, y) => raw[x] - raw[y])
  assert.deepEqual([...p.entries()].sort((x, y) => x[1] - y[1]).map((e) => e[0]), order)
}

// 8. Philadelphia, default weights (Rental income): S tier before (sales only) vs after (sales + listings)
{
  const data = JSON.parse(readFileSync(new URL('../../public/data/philadelphia/areas.json', import.meta.url)))
  const pool = data.areas.filter((a) => a.median12 != null && a.sales12 >= 30)
  const raw = {
    yield: (a) => a.yield, growth1: (a) => a.priceGrowth1 ?? a.medianYoY, growth5: (a) => a.priceGrowth5 ?? a.change5y,
    price: (a) => (a.median12 == null ? null : -a.median12), activity: (a) => a.sales12,
    crime: (a) => (a.violentRate == null || a.propertyRate == null ? null : -(a.violentRate * 3 + a.propertyRate)),
  }
  const build = (withListings) => {
    const pc = Object.fromEntries(F.map((f) => [f, f === 'activity' && withListings
      ? activityPercentiles(pool, (a) => a.key, (a) => a.sales12, [(a) => a.listings?.active ?? null, (a) => a.listings?.newListings ?? null])
      : percentiles(pool, (a) => a.key, raw[f])]))
    return pool.map((a) => ({ key: a.key, pctl: Object.fromEntries(F.filter((f) => pc[f].has(a.key)).map((f) => [f, pc[f].get(a.key)])) }))
  }
  const w = { yield: 5, growth1: 1, growth5: 1, price: 1, activity: 2, crime: 1 }
  const name = new Map(pool.map((a) => [a.key, a.name]))
  const before = rankPool(build(false), F, w).ranked, after = rankPool(build(true), F, w).ranked
  const fmt = (r) => r.filter((x) => x.tier === 'S').map((x) => `${name.get(x.key)} ${x.score}`).join(', ')
  console.log(`Philadelphia default weights, ${pool.filter((a) => a.listings).length}/${pool.length} areas with listing figures`)
  console.log('  S before:', fmt(before))
  console.log('  S after: ', fmt(after))
  const rb = new Map(before.map((x) => [x.key, x.rank]))
  const moves = after.map((x) => Math.abs(x.rank - rb.get(x.key)))
  const tierMoves = after.filter((x) => before.find((y) => y.key === x.key).tier !== x.tier).length
  console.log(`  rank change: median ${moves.sort((a, b) => a - b)[moves.length >> 1]}, max ${Math.max(...moves)}; ${tierMoves} of ${after.length} areas changed tier`)
}

console.log('all grades checks passed')
