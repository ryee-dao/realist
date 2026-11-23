// The live feed on the pipeline page. It calls each city's public API straight from the visitor's browser
// and applies the engine's keep-or-reject rules (mirrored from engine/schema.py and engine/sources/).

export interface LiveRow { date: string; address: string; kind: string; price: number | null; verdict: string | null }
export interface LiveResult { rows: LiveRow[]; ms: number; host: string; fetchedAt: number }

const MIN_PRICE = 10_000
const title = (s: unknown) => String(s ?? '').toLowerCase().replace(/\s+/g, ' ').trim().replace(/\b[a-z]/g, (c) => c.toUpperCase())
const priced = (price: number | null) => (price == null || price < MIN_PRICE ? 'Nominal price' : price > 100_000_000 ? 'Price above $100M' : null)

const SOURCES: Record<string, { url: string; map: (r: Record<string, unknown>) => LiveRow }> = {
  philadelphia: {
    url: 'https://phl.carto.com/api/v2/sql?q=' + encodeURIComponent(
      "select r.recording_date rec, r.street_address a, r.total_consideration price, r.property_count pc, o.category_code_description cat " +
      "from rtt_summary r left join opa_properties_public o on o.parcel_number = r.opa_account_num " +
      "where r.document_type = 'DEED' and r.recording_date is not null order by r.recording_date desc limit 40"),
    map: (r) => {
      const price = r.price == null ? null : Number(r.price)
      const cat = String(r.cat ?? '')
      const verdict = !cat ? 'No property record' : Number(r.pc ?? 1) > 1 ? 'Several properties'
        : priced(price) ?? (cat === 'SINGLE FAMILY' || cat === 'MULTI FAMILY' ? null : 'Not a home')
      return { date: String(r.rec).slice(0, 10), address: title(r.a), kind: title(cat) || 'Unknown', price, verdict }
    },
  },
  nyc: {
    url: 'https://data.cityofnewyork.us/resource/usep-8jbt.json?$query=' + encodeURIComponent(
      'select address, neighborhood, building_class_category, sale_price, sale_date order by sale_date desc, :id limit 40'),
    map: (r) => {
      const price = r.sale_price == null ? null : Number(String(r.sale_price).replace(/,/g, ''))
      const cat = String(r.building_class_category ?? '').trim()
      const home = ['01', '02', '03', '04', '09', '10', '12', '13', '15', '17'].includes(cat.slice(0, 2))
      return { date: String(r.sale_date).slice(0, 10), address: title(r.address), kind: title(cat.slice(3)) || 'Unknown', price, verdict: priced(price) ?? (home ? null : 'Not a home') }
    },
  },
  chicago: {
    url: 'https://datacatalog.cookcountyil.gov/resource/wvhk-k5uv.json?$query=' + encodeURIComponent(
      "select pin, class, sale_date, sale_price, deed_type, is_multisale, sale_filter_deed_type, sale_filter_same_sale_within_365 " +
      "where township_code in ('70','71','72','73','74','75','76','77') order by sale_date desc, row_id limit 40"),
    map: (r) => {
      const price = r.sale_price == null ? null : Number(r.sale_price)
      const cls = String(r.class ?? '')
      const home = cls === '299' || cls === '211' || ['202', '203', '204', '205', '206', '207', '208', '209', '210', '234', '278', '295'].includes(cls)
      const verdict = r.is_multisale ? 'Several properties' : r.sale_filter_deed_type ? 'Non-market deed'
        : r.sale_filter_same_sale_within_365 ? 'Duplicate sale' : priced(price) ?? (home ? null : 'Not a home')
      return { date: String(r.sale_date).slice(0, 10), address: `Parcel ${r.pin}`, kind: `Class ${cls} · ${title(r.deed_type)}`, price, verdict }
    },
  },
}

export async function fetchLive(city: string): Promise<LiveResult> {
  const src = SOURCES[city]
  const t0 = performance.now()
  const res = await fetch(src.url, { cache: 'no-store' })
  if (!res.ok) throw new Error(`The source answered ${res.status}`)
  const body = await res.json()
  const raw: Record<string, unknown>[] = Array.isArray(body) ? body : body.rows
  return { rows: raw.map(src.map), ms: Math.round(performance.now() - t0), host: new URL(src.url).host, fetchedAt: Date.now() }
}

export interface JobStatus { state: 'running' | 'queued' | 'done' | 'unknown'; ok: boolean; at: string | null; url: string | null }

/** Whether the daily GitHub job is running right now. Public API, no sign-in. */
export async function fetchJob(repoUrl: string): Promise<JobStatus> {
  try {
    const slug = repoUrl.replace('https://github.com/', '')
    const res = await fetch(`https://api.github.com/repos/${slug}/actions/workflows/pipeline.yml/runs?per_page=1`)
    if (!res.ok) throw new Error(String(res.status))
    const run = (await res.json()).workflow_runs?.[0]
    if (!run) return { state: 'unknown', ok: true, at: null, url: null }
    const state = run.status === 'in_progress' ? 'running' : run.status === 'completed' ? 'done' : 'queued'
    return { state, ok: run.conclusion !== 'failure', at: run.updated_at, url: run.html_url }
  } catch {
    return { state: 'unknown', ok: true, at: null, url: null }
  }
}
