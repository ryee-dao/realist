import { useEffect, useRef } from 'react'
import L from 'leaflet'
import { escapeHtml } from '../lib/safeUrl'

export interface ListingPin {
  id: string
  lat: number
  lon: number
  label: string      // short asking price, e.g. "$189K"
  age: 0 | 1 | 2     // 0 under 30 days, 1 30–90 days, 2 over 90 days
  tip: string
  dim?: boolean      // not on the current page of cards: a small faded dot
}

interface Props {
  center: [number, number]
  zoom: number
  pins: ListingPin[]
  active: string | null
  focus: string | null
  onHover: (id: string | null) => void
  onPick: (id: string) => void
  height: number | string
  visible: boolean   // re-measured when a hidden map is shown (phone Map/List toggle)
}

const TILES = 'https://tile.openstreetmap.org/{z}/{x}/{y}.png'

/** Price pins for listings. Age is shown by the pin's fill pattern (solid, light, hatched outline), not by colour alone. */
export default function ListingsMap({ center, zoom, pins, active, focus, onHover, onPick, height, visible }: Props) {
  const el = useRef<HTMLDivElement>(null)
  const map = useRef<L.Map | null>(null)
  const layer = useRef<L.LayerGroup | null>(null)
  const markers = useRef(new Map<string, L.Marker>())
  const made = useRef(new Map<string, string>())   // id -> what its marker was built from
  const fitted = useRef(false)
  // Pins of the current page keep their price label unless it would overlap one placed before it (they come first);
  // the rest shrink to a dot of the same age style, and hover shows the price. The active pin always sits on top.
  const declutter = useRef(() => {
    const m = map.current
    if (!m) return
    const placed: { x: number; y: number; w: number }[] = []
    markers.current.forEach((mk) => {
      const wrap = mk.getElement()
      const el = wrap?.firstElementChild as HTMLElement | null
      if (!wrap || !el) return
      const off = el.classList.contains('off')
      const pt = m.latLngToLayerPoint(mk.getLatLng())
      const w = (el.textContent?.length ?? 4) * 7 + 12
      const clash = off || placed.some((q) => Math.abs(q.x - pt.x) < (q.w + w) / 2 && Math.abs(q.y - pt.y) < 17)
      if (el.classList.contains('dot') !== clash) el.classList.toggle('dot', clash)
      const z = wrap.classList.contains('on') ? 3000 : off ? -2000 : clash ? -1000 : 0
      if (mk.options.zIndexOffset !== z) mk.setZIndexOffset(z)   // each call rewrites the marker's position: skip no-ops
      if (!clash) placed.push({ x: pt.x, y: pt.y, w })
    })
  })
  const cb = useRef({ onHover, onPick })
  cb.current = { onHover, onPick }

  useEffect(() => {
    const m = L.map(el.current!, { scrollWheelZoom: false, zoomSnap: 0.5 }).setView(center, zoom)
    m.on('click', () => m.scrollWheelZoom.enable())
    m.on('mouseout', () => m.scrollWheelZoom.disable())
    L.tileLayer(TILES, { attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors', maxZoom: 18 }).addTo(m)
    layer.current = L.layerGroup().addTo(m)
    m.on('zoomend', () => declutter.current())
    map.current = m
    return () => { m.remove(); map.current = null; markers.current.clear(); made.current.clear() }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Markers are kept across renders: paging only flips the faded style of the pins that change page, and a filter
  // adds or removes only the pins it changes. A pin is rebuilt only if its place, label, age or tip changes.
  useEffect(() => {
    const group = layer.current!
    const old = markers.current
    const next = new Map<string, L.Marker>()
    for (const p of pins) {
      const html = `<span class="lc-pin a${+p.age || 0}${p.dim ? ' off' : ''}">${escapeHtml(p.label)}</span>`   // Leaflet sets this as HTML
      const sig = `${p.lat},${p.lon}|${p.label}|${p.age}|${p.tip}`
      let mk = old.get(p.id)
      if (mk && made.current.get(p.id) !== sig) { group.removeLayer(mk); mk = undefined }
      if (mk) {
        const icon = mk.options.icon as L.DivIcon
        if (icon.options.html !== html) {
          icon.options.html = html   // what Leaflet would draw if it ever rebuilt the icon
          mk.getElement()?.firstElementChild?.classList.toggle('off', !!p.dim)
        }
      } else {
        const icon = L.divIcon({ className: 'lc-pin-wrap', iconSize: [0, 0], html })
        mk = L.marker([p.lat, p.lon], { icon, keyboard: false, riseOnHover: true, title: p.tip })
          .on('mouseover', () => cb.current.onHover(p.id))
          .on('mouseout', () => cb.current.onHover(null))
          .on('click', () => cb.current.onPick(p.id))
          .addTo(group)
        made.current.set(p.id, sig)
      }
      next.set(p.id, mk)
    }
    old.forEach((mk, id) => { if (!next.has(id)) { group.removeLayer(mk); made.current.delete(id) } })
    markers.current = next   // in pin order: the page's pins first, so declutter labels them first
    if (!fitted.current && pins.length && map.current) {
      fitted.current = true
      map.current.fitBounds(L.latLngBounds(pins.map((p) => [p.lat, p.lon] as [number, number])), { padding: [24, 24], maxZoom: 14 })
    }
    declutter.current()
  }, [pins])

  useEffect(() => {
    markers.current.forEach((mk, id) => mk.getElement()?.classList.toggle('on', id === active))
    declutter.current()
  }, [active, pins])

  useEffect(() => {
    if (!visible || !map.current) return
    const m = map.current
    const t = setTimeout(() => {
      m.invalidateSize()
      const ids = [...markers.current.values()]
      if (ids.length) m.fitBounds(L.latLngBounds(ids.map((mk) => mk.getLatLng())), { padding: [24, 24], maxZoom: 14 })
      declutter.current()
    }, 0)
    return () => clearTimeout(t)
  }, [visible])

  // a card that is picked brings its pin into view, without changing the zoom
  useEffect(() => {
    const mk = focus ? markers.current.get(focus) : null
    if (mk && map.current && !map.current.getBounds().pad(-0.1).contains(mk.getLatLng())) map.current.panTo(mk.getLatLng())
  }, [focus])

  return <div ref={el} className="map lc-map" style={{ height }} />
}
