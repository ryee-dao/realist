# Realist

Home-sale analytics for Philadelphia, New York City and Chicago, built from public records and refreshed every day by an automated pipeline.

**Live site: https://ryee-dao.github.io/realist/**

Realist shows what homes actually sold for, and helps turn that into a shortlist:

- **Find areas**: set a budget and weight what matters (yield, growth, entry price, market activity, reported incidents) to get a ranked list with the reasons for each rank.
- **Areas**: every neighbourhood ranked and mapped by price, growth, rent, gross rental yield and police-reported incidents per resident.
- **Market**: median prices, price per square foot and sales volume over time, by home type.
- **Crime**: police-reported incidents by neighbourhood, next to sale prices.
- **Recent sales**: individual sales compared with what is typical nearby.

No demographic data is used anywhere; areas are compared only on prices, rents, sales and reported incidents. The public [data pipeline page](https://ryee-dao.github.io/realist/#/pipeline) shows the newest records streaming in live from the source, and reports what each run read, kept and rejected and which quality checks it passed.

It runs with no server and no database bill. A scheduled GitHub Actions job runs the engine, stores the results in this repository, and publishes a static site to GitHub Pages.

## How it works

```
 open-data APIs ──▶ engine (Python) ──▶ store (data branch) ──▶ analytics JSON ──▶ static site (GitHub Pages)
   3 cities          harvest, validate     one CSV per city        quality gate       React + TypeScript
                                           per month
```

1. **Harvest.** One adapter per city pulls from the official open-data API. Requests are paced, retried with backoff, and skipped when the source has not changed since the last run. In Philadelphia, sales newer than the latest published deeds come from the city's property records, matched by deed document number.
2. **Validate.** Every record is mapped to one schema. A record is kept only if it has a valid sale date, a price of at least $10,000, a residential home type, and covers a single property. Every rejection is counted by reason.
3. **Store.** Sales are upserted into one gzipped CSV per city per month on the `data` branch. Files are written deterministically, so a month with no upstream change produces no diff.
4. **Analyse.** Monthly and quarterly statistics are rebuilt for each city, home type and area. The most recent months are left out until they hold at least 90% of the sales of the same month a year earlier.
5. **Context.** Police-reported incidents are counted per 500 m grid cell by the source APIs, residents come from Census block groups, and rents from a public rent index. Each area's catchment is everything nearer to its centre than to any other area's, so incident rates and populations line up.
6. **Quality gate.** The run fails, and the site is not updated, if records were lost, the data is stale, a median is implausible, or location coverage drops.

### Methods

The pure statistics live in `engine/stats.py` (standard library only).

- **Price growth.** Where an area has at least 100 usable repeat sales (and 25 sales at each end of the comparison), growth comes from a Bailey-Muth-Nourse repeat-sales index on 12-month periods: pairs of sales of the same home, at least a year apart, with co-ops, multi-unit parcels and changes beyond about 40% a year left out. Otherwise it falls back to the change in median. A citywide index is published too.
- **Median range.** Each area's 12-month median comes with a distribution-free 95% range from order statistics. The one-year change in median needs at least 20 sales in both years.
- **Incident rates.** Rates are shrunk toward the city rate with global empirical Bayes (Marshall 1991), so a small catchment does not swing on a few incidents. The unadjusted rate is kept alongside.
- **Incident change.** A year-on-year change is shown only when the exact test for two Poisson counts gives p < 0.05 on at least 20 incidents; otherwise it reads "no clear change".
- **Net yield.** A rule of thumb: half the gross yield (the investors' "50% rule" for taxes, insurance, repairs, vacancy and management).

### Data sources

| City | Source | Publisher |
| --- | --- | --- |
| Philadelphia | Real estate transfers, joined to property assessments | City of Philadelphia (OpenDataPhilly) |
| Philadelphia | Neighborhood boundaries | OpenDataPhilly |
| New York City | Citywide annualized and rolling calendar sales | NYC Department of Finance (NYC Open Data) |
| New York City | Recent deeds from the city register (ACRIS), classified with PLUTO, until Finance publishes them | NYC Department of Finance, Department of City Planning |
| Chicago | Assessor parcel sales, parcel universe, addresses, characteristics | Cook County Assessor's Office |
| All | Reported crime incidents | Philadelphia, New York City and Chicago police departments |
| All | Residents per block group (2020 Census) | U.S. Census Bureau |
| All | Observed Rent Index by ZIP code | Zillow Research |
| All | For-sale listings by ZIP code: inventory, new listings, median list price, share with a price cut (asking-side, shown apart from recorded sales) | Zillow Research |

## Run it yourself

The engine needs Python 3.11+ and nothing else. The site needs Node 20+.

```bash
# 1. Harvest and build the site's data (the first run pulls the full history and takes a while)
python -m engine run
python -m engine run --city philadelphia   # one city only
python -m engine run --full                # re-pull the whole history
python -m engine run --rebuild --city philadelphia   # wipe one city's stored sales and pull it again
python -m engine build                     # rebuild the site data from the store, no network

# 2. Run the site
cd web
npm install
npm run dev        # http://localhost:3000
npm run build      # static files in web/dist

# Tests
python -m unittest discover -s engine/tests
```

The store is written to `store/` and the site's data to `web/public/data/`. Both are git-ignored on `main`; the pipeline keeps the store on the `data` branch.

## Deployment

`.github/workflows/pipeline.yml` runs daily, on pushes to `main`, and on demand (with an optional full re-pull). It restores the store from the `data` branch, runs the tests and the engine, commits any new data, builds the site and deploys it to GitHub Pages.

One-time setup: in the repository settings, set **Pages → Build and deployment → Source** to **GitHub Actions**. Optionally add a `SOCRATA_APP_TOKEN` secret to raise API rate limits.

### Newly listed homes

The **New listings** page shows dated for-sale offers from Trulia’s public new-listing search pages for Philadelphia, NYC and Chicago. Each address links to the original offer. The daily build reads the Schema.org data embedded in the HTML and follows the published pagination, up to 30 pages per city. It uses each posting timestamp in the city's time zone for the Today, Yesterday and Last 7 days filters. Only offers with an available status, a USD asking price, an address and a property link are shown. Coverage depends on the results each city search returns.

Run `python3 -m engine.live_listings` to refresh all three cities or append `--city chicago` to refresh one. This fetch needs no credentials. If a later page fails or the page limit is reached, the website labels the snapshot as partial. If the first page fails, the website shows the unavailable state with a link to browse current listings on Redfin.

## Adding a city

Add a module in `engine/sources/` with a `harvest(ctx)` function that feeds `Normalizer.sale(...)` rows to a `SourceRun`, then register it in `engine/cities.py`. The store, analytics, quality checks and every page of the site pick it up from there.

## Project layout

```
engine/            harvest, validation, store and analytics (Python standard library only)
  sources/         one adapter per city
  tests/
web/               the static site (React, TypeScript, Vite, Leaflet; charts are hand-built SVG)
.github/workflows  the daily pipeline
```

## History

Version 1 was a Flask, Postgres, Celery and Redis application that scraped listing data and ran on a rented server. It is preserved at the [`v1-flask-app`](https://github.com/ryee-dao/realist/tree/v1-flask-app) tag. Version 2 replaces the scraper with open public records and the server with a scheduled pipeline.

## Notes

Figures are for research. They are not appraisals or financial advice. Public records lag the market by several weeks, and a median describes the homes that happened to sell in a period.
