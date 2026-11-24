/** DuckDB-WASM for the Explore page. Imported only from there (dynamically), so the engine, its worker and the
 *  wasm never touch the main bundle. Files are self-hosted by Vite (`?url`), so nothing loads from a CDN. */
import * as duckdb from '@duckdb/duckdb-wasm'
import mvpWasm from '@duckdb/duckdb-wasm/dist/duckdb-mvp.wasm?url'
import mvpWorker from '@duckdb/duckdb-wasm/dist/duckdb-browser-mvp.worker.js?url'
import ehWasm from '@duckdb/duckdb-wasm/dist/duckdb-eh.wasm?url'
import ehWorker from '@duckdb/duckdb-wasm/dist/duckdb-browser-eh.worker.js?url'
import { AREA_TYPES, SALES_TYPES } from './querySchema'

const BUNDLES: duckdb.DuckDBBundles = {
  mvp: { mainModule: mvpWasm, mainWorker: mvpWorker },
  eh: { mainModule: ehWasm, mainWorker: ehWorker },
}

export interface FileInfo { file: string; rows: number; bytes: number; rawBytes?: number; from?: string | null }
export interface QueryManifest {
  city: string; through: string; latestSale: string
  parquet?: FileInfo | null; full: FileInfo; recent: FileInfo & { years: number }; areas: FileInfo
}
export type Scope = 'parquet' | 'full' | 'recent'
export interface Progress { stage: 'engine' | 'download' | 'table'; loaded?: number; total?: number }

let dbPromise: Promise<duckdb.AsyncDuckDB> | null = null
let activeWorker: Worker | null = null
/** Rejects when the worker dies (out of memory, a wasm trap): DuckDB's own promises would otherwise never settle. */
let crashed: Promise<never> = new Promise(() => {})

const OOM = /memory|allocat|RangeError|Out of bounds|unreachable/i
export const isMemoryError = (msg: string) => OOM.test(msg)

/** Throw away the engine so the next load starts a fresh worker (after a crash or a cancelled load). */
export function reset() {
  activeWorker?.terminate()
  activeWorker = null
  dbPromise = null
  current = null
}

function watch(worker: Worker) {
  crashed = new Promise<never>((_, reject) => {
    const fail = (why: string) => { if (activeWorker === worker) { reset(); reject(new Error(why)) } }
    worker.addEventListener('error', (e) => fail(e.message || 'The query engine stopped (probably out of memory).'))
    worker.addEventListener('messageerror', () => fail('The query engine stopped responding.'))
  })
  crashed.catch(() => {})
}

/** Race a DuckDB call against the worker dying and a time limit. */
function guard<T>(p: Promise<T>, ms: number, what: string): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | undefined
  const late = new Promise<never>((_, reject) => { timer = setTimeout(() => reject(new Error(`${what} took longer than ${Math.round(ms / 1000)} s. The device may be short of memory.`)), ms) })
  return Promise.race([p, crashed, late]).finally(() => clearTimeout(timer))
}

async function engine(onProgress: (p: Progress) => void): Promise<duckdb.AsyncDuckDB> {
  if (typeof WebAssembly !== 'object' || typeof Worker !== 'function') throw new Error('This browser cannot run WebAssembly in a worker.')
  const bundle = await duckdb.selectBundle(BUNDLES)
  const worker = new Worker(bundle.mainWorker!)
  activeWorker = worker
  watch(worker)
  const db = new duckdb.AsyncDuckDB(new duckdb.VoidLogger(), worker)
  try {
    await db.instantiate(bundle.mainModule, bundle.pthreadWorker, (p) => onProgress({ stage: 'engine', loaded: p.bytesLoaded, total: p.bytesTotal }))
    // plain JS numbers out: no BigInt or Decimal objects in result rows
    await db.open({
      query: { castBigIntToDouble: true, castDecimalToDouble: true, castTimestampToDate: true },
      // read Parquet by HTTP range requests; fall back to one full download only if the server cannot serve ranges
      filesystem: { reliableHeadRequests: false, allowFullHTTPReads: true, forceFullHTTPReads: false },
    })
  } catch (e) {
    if (activeWorker === worker) reset()
    else worker.terminate()
    throw e
  }
  return db
}

function getDb(onProgress: (p: Progress) => void) {
  if (!dbPromise) dbPromise = engine(onProgress).catch((e) => { dbPromise = null; throw e })
  return guard(dbPromise, 90_000, 'Starting the engine')
}

const abs = (path: string) => new URL(path, window.location.href).href

/** Fetch with byte progress. Content-Length may be missing or describe the compressed transfer; progress is a guide. */
async function download(url: string, onProgress: (loaded: number, total: number, decoded: boolean) => void): Promise<Uint8Array> {
  const res = await fetch(url)
  if (!res.ok) throw new Error(`Could not download ${url.split('/').pop()} (HTTP ${res.status}).`)
  const total = +(res.headers.get('content-length') || 0)
  const decoded = !!res.headers.get('content-encoding')   // the body arrives uncompressed, so Content-Length undercounts it
  if (!res.body) return new Uint8Array(await res.arrayBuffer())
  const reader = res.body.getReader()
  const parts: Uint8Array[] = []
  let loaded = 0
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    parts.push(value)
    loaded += value.length
    onProgress(loaded, total, decoded)
  }
  const out = new Uint8Array(loaded)
  let at = 0
  for (const p of parts) { out.set(p, at); at += p.length }
  return out
}

const isGzip = (b: Uint8Array) => b.length > 2 && b[0] === 0x1f && b[1] === 0x8b
const columns = (types: Record<string, string>) => `{${Object.entries(types).map(([c, t]) => `'${c}': '${t}'`).join(', ')}}`

async function csvTable(db: duckdb.AsyncDuckDB, conn: duckdb.AsyncDuckDBConnection, table: string, bytes: Uint8Array, types: Record<string, string>) {
  // a server may already have undone the gzip (Content-Encoding), so look at the bytes rather than the file name;
  // the dialect is fixed (Python's csv module doubles quotes), as the sniffer can miss "" in a sample
  const gz = isGzip(bytes)
  const name = `${table}.csv${gz ? '.gz' : ''}`
  await db.registerFileBuffer(name, bytes)
  await guard(conn.query(`CREATE OR REPLACE TABLE ${table} AS SELECT * FROM read_csv('${name}', header = true, delim = ',', quote = '"', escape = '"', columns = ${columns(types)}, nullstr = '', compression = '${gz ? 'gzip' : 'none'}')`), 120_000, `Building the ${table} table`)
  await db.dropFile(name)
}

export interface Loaded { conn: duckdb.AsyncDuckDBConnection; rows: number; scope: Scope; bytes: number; ms: { engine: number; download: number; table: number } }

let current: { db: duckdb.AsyncDuckDB; conn: duckdb.AsyncDuckDBConnection } | null = null

/** Start DuckDB, then create `sales` (a table from CSV, or a view over Parquet read by HTTP range requests) and `areas`. */
export async function loadCity(city: string, manifest: QueryManifest, scope: Scope, onProgress: (p: Progress) => void): Promise<Loaded> {
  const t0 = performance.now()
  const db = await getDb(onProgress)
  const t1 = performance.now()
  if (current) await current.conn.close().catch(() => {})
  const conn = await guard(db.connect(), 20_000, 'Connecting')
  current = { db, conn }
  const base = `data/${city}/`
  // bytes as published (compressed): what a download costs, whatever the server does with Content-Encoding
  let bytes = manifest.areas.bytes
  const areaBytes = await download(base + manifest.areas.file, () => {})
  let salesBytes: Uint8Array | null = null
  if (scope !== 'parquet') {
    const info = scope === 'full' ? manifest.full : manifest.recent
    salesBytes = await download(base + info.file, (loaded, total, decoded) =>
      onProgress({ stage: 'download', loaded, total: decoded ? info.rawBytes ?? loaded : total || info.bytes }))
    bytes += info.bytes
  }
  const t2 = performance.now()
  onProgress({ stage: 'table' })
  await csvTable(db, conn, 'areas', areaBytes, AREA_TYPES)
  if (salesBytes) {
    await csvTable(db, conn, 'sales', salesBytes, SALES_TYPES)
  } else {
    const name = `${city}-sales.parquet`
    await db.registerFileURL(name, abs(base + manifest.parquet!.file), duckdb.DuckDBDataProtocol.HTTP, false)
    await conn.query(`CREATE OR REPLACE VIEW sales AS SELECT * FROM read_parquet('${name}')`)
  }
  const count = await guard(conn.query('SELECT count(*) AS n FROM sales'), 60_000, 'Counting sales')
  const rows = Number(count.getChildAt(0)?.get(0) ?? 0)
  return { conn, rows, scope, bytes, ms: { engine: t1 - t0, download: t2 - t1, table: performance.now() - t2 } }
}

export type Kind = 'num' | 'date' | 'text'
export interface Result { columns: { name: string; kind: Kind }[]; rows: unknown[][]; total: number; ms: number; csv: () => string }

const day = (ms: number) => new Date(ms).toISOString().slice(0, 10)

function cell(v: unknown, kind: Kind): unknown {
  if (v == null) return null
  if (typeof v === 'bigint') return Number(v)
  if (kind === 'date' && typeof v === 'number') return day(v)
  if (v instanceof Date) return day(v.getTime())
  if (typeof v === 'object') {
    const j = (v as { toJSON?: () => unknown }).toJSON?.() ?? v
    return JSON.stringify(j, (_, x) => (typeof x === 'bigint' ? Number(x) : x))
  }
  return v
}

const csvCell = (v: unknown) => {
  if (v == null) return ''
  const s = String(v)
  return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s
}

// One streaming query at a time per connection: a second send() invalidates the first one's stream.
const queues = new WeakMap<duckdb.AsyncDuckDBConnection, Promise<unknown>>()

/** Run SQL, keeping the first `cap` rows for display and the true row count. Cancel with `cancel()`. */
export function run(conn: duckdb.AsyncDuckDBConnection, sql: string, cap = 1000): Promise<Result> {
  const next = (queues.get(conn) ?? Promise.resolve()).catch(() => {}).then(() => runNow(conn, sql, cap))
  queues.set(conn, next)
  return next
}

async function runNow(conn: duckdb.AsyncDuckDBConnection, sql: string, cap: number): Promise<Result> {
  const t0 = performance.now()
  const reader = await Promise.race([conn.send(sql), crashed])
  const batches: { numRows: number; schema: unknown; get: (i: number) => unknown }[] = []
  let total = 0
  let fields: { name: string; kind: Kind }[] = []
  const rows: unknown[][] = []
  for await (const batch of reader) {
    if (!fields.length) fields = batch.schema.fields.map((f) => ({ name: f.name, kind: kindOf(f.type.typeId) }))
    batches.push(batch as never)
    for (let i = 0; i < batch.numRows && rows.length < cap; i++) {
      const r = batch.get(i)!.toArray()
      rows.push(fields.map((f, j) => cell(r[j], f.kind)))
    }
    total += batch.numRows
  }
  if (!fields.length) fields = reader.schema?.fields.map((f) => ({ name: f.name, kind: kindOf(f.type.typeId) })) ?? []
  const ms = performance.now() - t0
  const csv = () => {
    const out = [fields.map((f) => csvCell(f.name)).join(',')]
    for (const b of batches as unknown as { numRows: number; get: (i: number) => { toArray: () => unknown[] } }[]) {
      for (let i = 0; i < b.numRows; i++) out.push(b.get(i).toArray().map((v, j) => csvCell(cell(v, fields[j].kind))).join(','))
    }
    return out.join('\n') + '\n'
  }
  return { columns: fields, rows, total, ms, csv }
}

export async function cancel(conn: duckdb.AsyncDuckDBConnection) {
  try { await conn.cancelSent() } catch { /* nothing running */ }
}

// apache-arrow Type ids: Int 2, Float 3, Decimal 7, Date 8, Time 9, Timestamp 10
function kindOf(typeId: number): Kind {
  if (typeId === 2 || typeId === 3 || typeId === 7) return 'num'
  if (typeId === 8 || typeId === 10) return 'date'
  return 'text'
}
