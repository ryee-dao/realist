import { startTransition, useEffect, useLayoutEffect, useRef, useState } from 'react'

/** Width of an element, tracked as it resizes. */
export function useWidth<T extends HTMLElement>(): [React.RefObject<T>, number] {
  const ref = useRef<T>(null)
  const [width, setWidth] = useState(0)
  useLayoutEffect(() => {
    const el = ref.current
    if (!el) return
    setWidth(el.clientWidth)
    const ro = new ResizeObserver(() => setWidth(el.clientWidth))
    ro.observe(el)
    return () => ro.disconnect()
  }, [])
  return [ref, width]
}

export interface Route { city: string | null; page: string }

function parse(): Route {
  // a page may keep its own state after '?' in the hash (Explore does), so the route is the part before it
  const parts = window.location.hash.replace(/^#\/?/, '').split('?')[0].split('/').filter(Boolean)
  if (parts[0] === 'pipeline' || parts[0] === 'about') return { city: null, page: parts[0] }
  return { city: parts[0] || null, page: parts[1] || 'find' }
}

export function useRoute(): Route {
  const [route, setRoute] = useState(parse)
  useEffect(() => {
    // a transition: while the next page's code loads, the current page stays on screen instead of a placeholder
    const on = () => { startTransition(() => setRoute(parse())); window.scrollTo(0, 0) }
    window.addEventListener('hashchange', on)
    return () => window.removeEventListener('hashchange', on)
  }, [])
  return route
}

export type Theme = 'light' | 'dark'
const THEME_KEY = 'realist-theme'

const applyTheme = (t: Theme) => {
  document.documentElement.dataset.theme = t
  document.querySelector('meta[name="theme-color"]')?.setAttribute('content', t === 'light' ? '#eef2ef' : '#121416')
}

/** Light or dark. index.html sets data-theme before first paint (?theme=, else the saved choice, else dark);
 *  this mirrors it in state so maps and canvases redraw when it changes. Returns the theme and a toggle. */
export function useTheme(): [Theme, () => void] {
  const [theme, setTheme] = useState<Theme>(() => (document.documentElement.dataset.theme === 'light' ? 'light' : 'dark'))
  const toggle = () => {
    const next: Theme = theme === 'light' ? 'dark' : 'light'
    applyTheme(next)
    try { localStorage.setItem(THEME_KEY, next) } catch { /* private window */ }
    setTheme(next)
  }
  return [theme, toggle]
}

/** Read a CSS custom property so charts and the map follow the active theme. */
export const cssVar = (name: string) => getComputedStyle(document.documentElement).getPropertyValue(name).trim()
