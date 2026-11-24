import { useEffect, useRef } from 'react'
import L from 'leaflet'

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
      el.classList.toggle('dot', clash)
      mk.setZIndexOffset(wrap.classList.contains('on') ? 3000 : off ? -2000 : clash ? -1000 : 0)
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
    return () => { m.remove(); map.current = null }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    const group = layer.current!
    group.clearLayers()
    markers.current.clear()
    for (const p of pins) {
      const icon = L.divIcon({ className: 'lc-pin-wrap', iconSize: [0, 0], html: `<span class="lc-pin a${p.age}${p.dim ? ' off' : ''}">${p.label}</span>` })
      const mk = L.marker([p.lat, p.lon], { icon, keyboard: false, riseOnHover: true, title: p.tip })
        .on('mouseover', () => cb.current.onHover(p.id))
        .on('mouseout', () => cb.current.onHover(null))
        .on('click', () => cb.current.onPick(p.id))
        .addTo(group)
      markers.current.set(p.id, mk)
    }
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
