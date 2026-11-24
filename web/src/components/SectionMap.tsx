import { useEffect, useRef } from 'react'
import L from 'leaflet'
import { City, Grid } from '../lib/data'
import { cssVar, Theme } from '../lib/hooks'

/** How one area's cells are filled. `color` must be a resolved colour (canvas cannot read CSS variables). */
export interface SectionFill { color: string; opacity?: number }

/**
 * Neighbourhoods drawn as filled sections: each grid cell (grid.json) is a small square coloured by its area,
 * with the selected area outlined. Used by the Crime and Find areas pages.
 * `fillOf` and `tipOf` take an index into areas.json; return null from `fillOf` to leave an area unfilled.
 * Memoise both (they are effect dependencies) and include the theme in their inputs so colours follow it.
 */
export default function SectionMap({ city, theme, grid, fillOf, tipOf, selected, onSelect, className = '', height }: {
  city: City; theme: Theme; grid: Grid
  fillOf: (areaIndex: number) => SectionFill | null
  tipOf: (areaIndex: number) => string   // HTML; escape any text with escapeHtml
  selected: number | null; onSelect: (areaIndex: number) => void
  className?: string; height?: number
}) {
  const el = useRef<HTMLDivElement>(null)
  const map = useRef<L.Map | null>(null)
  const fill = useRef<L.LayerGroup | null>(null)
  const outline = useRef<L.LayerGroup | null>(null)
  const renderer = useRef<L.Canvas | null>(null)
  const select = useRef(onSelect)
  select.current = onSelect
  const tip = useRef(tipOf)
  tip.current = tipOf

  useEffect(() => {
    const m = L.map(el.current!, { scrollWheelZoom: false, zoomSnap: 0.5 }).setView(city.center, city.zoom)
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors', maxZoom: 17,
    }).addTo(m)
    m.on('click', () => m.scrollWheelZoom.enable())
    m.on('mouseout', () => m.scrollWheelZoom.disable())
    renderer.current = L.canvas({ padding: 0.5 })
    fill.current = L.layerGroup().addTo(m)
    outline.current = L.layerGroup().addTo(m)
    map.current = m
    const g = grid.columns
    const pts: L.LatLngTuple[] = []
    for (let i = 0; i < g.lat.length; i++) if (g.area[i] >= 0) pts.push([g.lat[i], g.lon[i]])
    if (pts.length) m.fitBounds(L.latLngBounds(pts), { padding: [8, 8] })
    return () => { m.remove(); map.current = null }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    const group = fill.current!
    group.clearLayers()
    const g = grid.columns
    const h = grid.cell / 2
    const fills = new Map<number, SectionFill | null>()
    for (let i = 0; i < g.lat.length; i++) {
      const ai = g.area[i]
      if (ai < 0) continue
      if (!fills.has(ai)) fills.set(ai, fillOf(ai))
      const f = fills.get(ai)
      L.rectangle([[g.lat[i] - h, g.lon[i] - h], [g.lat[i] + h, g.lon[i] + h]], {
        renderer: renderer.current!, stroke: false,
        fillColor: f ? f.color : cssVar('--surface-2'), fillOpacity: f ? f.opacity ?? 0.85 : 0,
      })
        .bindTooltip(() => tip.current(ai), { className: 'map-tip', sticky: true })
        .on('click', (e) => { L.DomEvent.stopPropagation(e); select.current(ai) })
        .addTo(group)
    }
  }, [grid, fillOf, theme])

  // Outline the selected area: draw only the cell edges that border another area.
  useEffect(() => {
    const group = outline.current!
    group.clearLayers()
    if (selected == null) return
    const g = grid.columns
    const s = grid.cell
    const h = s / 2
    const key = (la: number, lo: number) => `${Math.round(la / s)},${Math.round(lo / s)}`
    const mine = new Set<string>()
    for (let i = 0; i < g.lat.length; i++) if (g.area[i] === selected) mine.add(key(g.lat[i], g.lon[i]))
    const segs: L.LatLngTuple[][] = []
    for (let i = 0; i < g.lat.length; i++) {
      if (g.area[i] !== selected) continue
      const la = g.lat[i], lo = g.lon[i]
      if (!mine.has(key(la + s, lo))) segs.push([[la + h, lo - h], [la + h, lo + h]])
      if (!mine.has(key(la - s, lo))) segs.push([[la - h, lo - h], [la - h, lo + h]])
      if (!mine.has(key(la, lo + s))) segs.push([[la - h, lo + h], [la + h, lo + h]])
      if (!mine.has(key(la, lo - s))) segs.push([[la - h, lo - h], [la + h, lo - h]])
    }
    if (segs.length) L.polyline(segs, { renderer: renderer.current!, color: cssVar('--text'), weight: 2.5, interactive: false }).addTo(group)
  }, [grid, selected, theme])

  return <div ref={el} className={`map ${className}`} style={height ? { height } : undefined} />
}

export const escapeHtml = (s: string) => s.replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]!))
