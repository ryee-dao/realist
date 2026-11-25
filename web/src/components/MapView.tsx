import { useEffect, useRef } from 'react'
import L from 'leaflet'
import '../styles/scales.css'
import { cssVar, Theme } from '../lib/hooks'
import { escapeHtml } from '../lib/safeUrl'

export interface MapPoint {
  id: string | number
  lat: number
  lon: number
  radius: number
  color: string
  tip: string        // plain text shown on hover
}

interface Props {
  center: [number, number]
  zoom: number
  points: MapPoint[]
  theme: Theme
  selected?: string | number | null
  onSelect?: (id: string | number) => void
  height?: number
}

// OpenStreetMap's standard tiles; the dark theme tones them down with a CSS filter (see styles.css).
const TILES = 'https://tile.openstreetmap.org/{z}/{x}/{y}.png'

/** On touch screens a map inside a long page must not trap scrolling: one finger scrolls the page,
 *  two fingers move and zoom the map (Leaflet's pinch follows the fingers), and a one-finger drag
 *  briefly shows a hint saying so (styles.css, .map-hint). */
export function touchFriendly(m: L.Map) {
  if (!(L.Browser.mobile || window.matchMedia?.('(pointer: coarse)').matches)) return
  m.dragging.disable()
  const box = m.getContainer()
  let t = 0
  box.addEventListener('touchmove', (e) => {
    box.classList.toggle('map-hint', e.touches.length === 1)
    window.clearTimeout(t)
    t = window.setTimeout(() => box.classList.remove('map-hint'), 1200)
  }, { passive: true })
}

export default function MapView({ center, zoom, points, theme, selected, onSelect, height = 460 }: Props) {
  const el = useRef<HTMLDivElement>(null)
  const map = useRef<L.Map | null>(null)
  const tiles = useRef<L.TileLayer | null>(null)
  const layer = useRef<L.LayerGroup | null>(null)
  const select = useRef(onSelect)
  select.current = onSelect

  useEffect(() => {
    const m = L.map(el.current!, { preferCanvas: true, scrollWheelZoom: false, zoomSnap: 0.5 }).setView(center, zoom)
    m.on('click', () => m.scrollWheelZoom.enable())
    m.on('mouseout', () => m.scrollWheelZoom.disable())
    touchFriendly(m)
    layer.current = L.layerGroup().addTo(m)
    map.current = m
    return () => { m.remove(); map.current = null; tiles.current = null }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => { map.current?.setView(center, zoom) }, [center[0], center[1], zoom])

  useEffect(() => {
    if (tiles.current) return
    tiles.current = L.tileLayer(TILES, {
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors', maxZoom: 18,
    }).addTo(map.current!)
  }, [])

  useEffect(() => {
    const group = layer.current!
    group.clearLayers()
    const ring = cssVar('--surface')
    const ink = cssVar('--text')
    for (const p of points) {
      const on = p.id === selected
      L.circleMarker([p.lat, p.lon], {
        radius: p.radius + (on ? 2 : 0), fillColor: p.color, fillOpacity: 0.9,
        color: on ? ink : ring, weight: on ? 2.5 : 1,
      })
        .bindTooltip(escapeHtml(p.tip), { direction: 'top', offset: [0, -p.radius], className: 'map-tip' })   // Leaflet reads a string as HTML
        .on('click', (e) => { L.DomEvent.stopPropagation(e); select.current?.(p.id) })
        .addTo(group)
    }
  }, [points, selected, theme])

  return <div ref={el} className="map" style={{ height }} />
}

/** Shared map colour scales (tokens in styles/scales.css): `--${SCALE.x}-1..5`, step 5 = most / highest. */
export const SCALE = { incidents: 'inc', change: 'chg', price: 'price' } as const

/** Split values into five equal-count classes and return the upper bound of each. */
export function quintiles(values: number[]): number[] {
  const v = [...values].sort((a, b) => a - b)
  return [0.2, 0.4, 0.6, 0.8, 1].map((q) => v[Math.min(v.length - 1, Math.floor(q * (v.length - 1)))])
}

export const classOf = (value: number, bounds: number[]) => {
  const i = bounds.findIndex((b) => value <= b)
  return i < 0 ? bounds.length - 1 : i
}
