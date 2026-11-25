import L from 'leaflet'

/**
 * Keeps hover labels inside the map. Leaflet centres a label on its marker and the map clips whatever sticks out, so a
 * label near an edge lost its first or last words. This nudges the label sideways to fit, and drops it below the marker
 * when there is no room above. (Long labels also wrap: see .map-tip in styles.css.)
 */
export function keepTipsInside(map: L.Map) {
  let open: L.Tooltip | null = null
  const fit = () => {
    const el = open?.getElement()
    if (!open || !el) return
    el.style.marginLeft = el.style.marginTop = ''          // back to Leaflet's own margins before measuring
    const own = getComputedStyle(el)
    const r = el.getBoundingClientRect(), m = map.getContainer().getBoundingClientRect(), pad = 4
    let dx = 0, dy = 0
    if (r.left < m.left + pad) dx = m.left + pad - r.left
    else if (r.right > m.right - pad) dx = m.right - pad - r.right
    if (r.top < m.top + pad) {
      const off = open.options.offset as [number, number] | undefined
      // a label that sits above its marker goes below it instead; one that follows the pointer just moves down
      dy = open.options.direction === 'top' ? r.height + 2 * Math.abs(off?.[1] ?? 0) + 12 : m.top + pad - r.top
    } else if (r.bottom > m.bottom - pad) dy = m.bottom - pad - r.bottom
    // the zoom buttons sit above every label, so a label that would slide under them moves to their right
    const zoom = map.getContainer().querySelector('.leaflet-control-zoom')?.getBoundingClientRect()
    if (zoom && r.left + dx < zoom.right + pad && r.top + dy < zoom.bottom + pad && r.right + dx > zoom.left && r.bottom + dy > zoom.top)
      dx = zoom.right + pad + 2 - r.left
    if (dx) el.style.marginLeft = `${parseFloat(own.marginLeft) + dx}px`
    if (dy) el.style.marginTop = `${parseFloat(own.marginTop) + dy}px`
  }
  map.on('tooltipopen', (e) => { open = e.tooltip; fit() })
  map.on('tooltipclose', (e) => { if (open === e.tooltip) open = null })
  map.on('mousemove', fit)                                  // labels that follow the pointer are placed again as it moves
}
