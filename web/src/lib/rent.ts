// Plain-words rent notes shared by every page that shows rent or rent yield. The figures come from engine/rents.py:
// `rent` is the headline (midpoint of what tenants pay, brought to today, and asking rents), `rentYoY` the asking trend.
import type { RentPicture } from './data'
import { pct, usd } from './format'

type WithRent = RentPicture & { rent: number | null; rentYoY: number | null; yield?: number | null }

const bed = (k: string) => (k === '0' ? 'studio' : `${k === '5' ? '5+' : k} bd`)

/** "1 bd $1,050 · 2 bd $1,300 · 3 bd $1,600": HUD fair market rents where published, else the Census survey. */
export function bedsLine(r: RentPicture): string | null {
  const hud = r.rentHud?.beds, census = r.rentPaid?.reliable ? r.rentPaid.beds : null
  const beds = hud && Object.keys(hud).length ? hud : census
  if (!beds) return null
  const common = ['1', '2', '3'].filter((k) => beds[k] != null)     // keep it to one short line
  const keys = common.length ? common : Object.keys(beds).sort()
  if (!keys.length) return null
  const line = keys.map((k) => `${bed(k)} ${usd(beds[k])}`).join(' · ')
  return beds === hud ? `${line} (HUD${r.rentHud?.year ? ` FY${r.rentHud.year}` : ''}, recent movers)` : `${line} (Census survey ${r.rentPaid?.period ?? ''})`
}

/** The figures behind the headline, one short line each. */
export function rentLines(r: WithRent, city?: WithRent | null): string[] {
  const out: string[] = []
  const p = r.rentPaid
  if (p?.reliable && p.today != null) out.push(`Tenants pay about ${usd(p.today)} (Census, brought to today)`)
  else if (p?.reliable) out.push(`Tenants paid ${usd(p.value)} (Census survey ${p.period})`)
  else if (p) out.push(`Census survey too uncertain here`)
  if (r.rentAsking != null) out.push(`Asking ${usd(r.rentAsking)} (Zillow)${r.rentYoY != null ? ` · ${r.rentYoY >= 0 ? 'up' : 'down'} ${Math.abs(r.rentYoY).toFixed(1)}% in a year` : ''}`)
  else if (r.rentYoY != null) out.push(`Asking rents ${pct(r.rentYoY)} in a year (Zillow)`)
  const beds = bedsLine(r)
  if (beds) out.push(beds)
  if (r.rentBasis === 'blend') out.push(`Shown: the midpoint${r.rentDisagree ? '. Sources disagree by over 25%' : ''}`)
  else if (r.rentBasis === 'asking' && p) out.push('Shown: asking rent only')
  else if (r.rentBasis === 'survey') out.push('Shown: Census survey, not brought to today')
  const metro = city?.rentHud?.metroWide && !r.rentHud ? city.rentHud : null
  if (metro?.beds['2'] != null) out.push(`HUD fair rent, whole metro: 2 bd ${usd(metro.beds['2'])}${metro.year ? ` (FY${metro.year})` : ''}`)
  if (city?.rent != null) out.push(`City ${usd(city.rent)}`)
  return out
}

/** "$1,330/mo": the headline as a row value. */
export const rentValue = (r: WithRent) => (r.rent == null ? 'no estimate' : `${usd(r.rent)}/mo`)

/** "5.1–6.4% from what tenants pay to asking rents", when the rent has a range. */
export function yieldRange(r: WithRent): string | null {
  if (r.yieldLow == null || r.yieldHigh == null) return null
  return `${r.yieldLow.toFixed(1)}–${r.yieldHigh.toFixed(1)}% from what tenants pay to asking rents`
}
