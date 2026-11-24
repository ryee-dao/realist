/** Column types of the Explore tables, in step with QUERY_TYPES and AREA_COLUMNS in engine/analytics.py.
 *  Kept apart from lib/duck.ts so the page can list them before DuckDB has loaded. */
export const SALES_TYPES: Record<string, string> = {
  date: 'DATE', price: 'INTEGER', type: 'VARCHAR', address: 'VARCHAR', zip: 'VARCHAR', area: 'VARCHAR', sqft: 'INTEGER',
  year_built: 'INTEGER', beds: 'INTEGER', lat: 'DOUBLE', lon: 'DOUBLE', assessed: 'BIGINT', parcel: 'VARCHAR',
}
export const AREA_TYPES: Record<string, string> = {
  area: 'VARCHAR', area_group: 'VARCHAR', sales_12m: 'INTEGER', median_12m: 'INTEGER', median_12m_low: 'INTEGER',
  median_12m_high: 'INTEGER', median_yoy_pct: 'DOUBLE', ppsf_12m: 'INTEGER', growth_1y_pct: 'DOUBLE', growth_5y_pct: 'DOUBLE',
  growth_method: 'VARCHAR', top_type: 'VARCHAR', rent: 'INTEGER', rent_asking: 'INTEGER',
  rent_paid_survey: 'INTEGER', rent_paid_today: 'INTEGER', gross_yield_pct: 'DOUBLE', lat: 'DOUBLE', lon: 'DOUBLE',
}
