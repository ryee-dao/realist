/** Links that come from data (publishers' listing pages, photo credits) are only ever http(s) and absolute: a
 *  `javascript:`, `data:` or relative value from a hostile publisher becomes undefined, so React renders no href.
 *  React 18 still renders `javascript:` URLs (it only warns), so every data-driven href goes through this. */
export function safeUrl(url: unknown): string | undefined {
  if (typeof url !== 'string') return undefined
  try {
    const u = new URL(url)
    return u.protocol === 'https:' || u.protocol === 'http:' ? u.href : undefined
  } catch {
    return undefined
  }
}

/** A path under our own data/ folder for a file named in data (crops): relative, no scheme, no "..", no "//". */
export function dataPath(path: unknown): string | undefined {
  if (typeof path !== 'string' || !/^[A-Za-z0-9_\-./]+$/.test(path) || path.startsWith('/') || path.includes('..') || path.includes('//')) return undefined
  return `data/${path}`
}

/** Text for an HTML string (Leaflet tooltips and marker icons take HTML). */
export const escapeHtml = (s: unknown) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]!))
