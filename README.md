# Realist

Home-sale analytics for Philadelphia, New York City and Chicago, built from public records and refreshed every day by an automated pipeline.

**Live site: https://ryee-dao.github.io/realist/**

Realist shows what homes actually sold for, and helps turn that into a shortlist:

- **Find areas**: set a budget and weight what matters (yield, growth, entry price, market activity, reported incidents) to get a ranked list with the reasons for each rank.
- **Areas**: every neighbourhood ranked and mapped by price, growth, rent, gross rental yield and police-reported incidents per resident.
- **Market**: median prices, price per square foot and sales volume over time, by home type.
- **Price drivers** (Philadelphia): what moves recorded sale prices, and how close a model gets compared with the assessor's value.
- **Crime**: police-reported incidents by neighbourhood, next to sale prices.
- **Recent sales**: individual sales compared with what is typical nearby.
- **Explore**: every recorded sale, queried in the browser with DuckDB: a filter-and-group builder, one-click questions, a SQL editor, charts and CSV download, with the query kept in a shareable link.

No demographic data is used anywhere; areas are compared only on prices, rents, sales and reported incidents. The public [data pipeline page](https://ryee-dao.github.io/realist/#/pipeline) replays the newest records the engine read on its latest run, with the verdict each got, and reports what each run read, kept and rejected and which quality checks it passed.

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

The Explore page loads DuckDB-WASM (self-hosted, only on that page) and queries `<city>/sales.parquet` over HTTP range requests when the engine wrote it, otherwise the gzipped CSV of the same sales (the last three years by default on small screens).

### Methods

The pure statistics live in `engine/stats.py` (standard library only).

- **Price growth.** Where an area has at least 100 usable repeat sales (and 25 sales at each end of the comparison), growth comes from a Bailey-Muth-Nourse repeat-sales index on 12-month periods: pairs of sales of the same home, at least a year apart, with co-ops, multi-unit parcels and changes beyond about 40% a year left out. Otherwise it falls back to the change in median. A citywide index is published too.
- **Median range.** Each area's 12-month median comes with a distribution-free 95% range from order statistics. The one-year change in median needs at least 20 sales in both years.
- **Incident rates.** Rates are shrunk toward the city rate with global empirical Bayes (Marshall 1991), so a small catchment does not swing on a few incidents. The unadjusted rate is kept alongside.
- **Incident change.** A year-on-year change is shown only when the exact test for two Poisson counts gives p < 0.05 on at least 20 incidents; otherwise it reads "no clear change".
- **Net yield.** A rule of thumb: half the gross yield (the investors' "50% rule" for taxes, insurance, repairs, vacancy and management).
- **Price drivers.** A hedonic regression on log price (`engine/model.py`): floor and lot area, age, rooms, condition and quality ratings, building form and fittings from the city's property records, plus neighbourhood and sale-quarter fixed effects and the typical gap of the 15 nearest sales in the two years before. Missing values get their own level; extreme residuals are trimmed and the model refitted once; intervals use robust (HC1) standard errors. It is fitted on sales through 2024 and scored on later sales against the assessor's value certified before each sale and an area price-per-square-foot rule. Characteristics are the current record, not the record at the time of sale.

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

The engine needs Python 3.11+ and no other packages; `duckdb` (Parquet export for the Explore page) and `pillow` (listing photo crops) are optional. The site needs Node 20+.

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

The listing harvester reads public, server-rendered pages without API keys, identifying itself as `Realist/2.0 (public housing data research)`. It fetches only paths that the publisher's robots.txt allows for a generic crawler, waits at least one second between requests, and stops at small page budgets. When a publisher answers HTTP 403 or 429, or serves a bot-check page, the harvester backs off. It does not retry around a refusal, switch user agents, or use proxies. The harvester records photo URLs and does not download images. The one exception is listings queued for photo analysis. Their photos are fetched under the same rules, shrunk to at most 1024 px and held privately for a short time. They are never published and are deleted once the listing has been analysed. Only tight defect crops are published.

| Publisher | Philadelphia | NYC | Chicago | List date from |
|---|:-:|:-:|:-:|---|
| Movoto | ✓ | ✓ | ✓ | search results (up to 8 pages) |
| Weichert | ✓ | ✓ | ✓ | each property page's listing history (up to 16) |
| Coldwell Banker Realty | ✓ | ✓ | ✓ | each property page's `datePosted` (up to 16) |
| EveryHome | ✓ | | | search cards |
| @properties | | | ✓ | the Chicagoland just-listed search (one page) |
| OneKey MLS | | ✓ | | five borough/area result pages, 30 listings each |
| Northrop Realty | ✓ | | | Philadelphia property pages, with a five-second interval |
| Corcoran Urban Real Estate | | | ✓ | published listing history on Chicago property pages |
| Trulia | paused | paused | paused | — answered HTTP 403 to every request in November 2026 |

Each run reads one publisher per city: the eligible publisher that was tried longest ago. It moves on to the next publisher only when that read fails or finds no listings. Every publisher therefore gets a turn before any publisher repeats. A refusal (403/429 or a bot-check page) puts that publisher on a cool-down of 1 day, then 3 days, then 7 days for each further consecutive refusal. A success clears the cool-down. Each city's `harvest-state.json` keeps, for each publisher, the last attempt, the last success, the consecutive failures, any cool-down, and the last error. A city snapshot's `attention` list names publishers that are paused, cooling down, or have failed three runs in a row. In Actions, these show up as warnings.

```bash
python3 -m engine.live_listings                               # one publisher per city, by rotation
python3 -m engine.live_listings --city chicago --source atproperties --details 40
python3 -m engine.live_listings --source everyhome --output /tmp/realist-listings
```

`--source` reads that publisher immediately, ignoring the rotation and any cool-down. It still records the outcome.

Under the output directory (default `web/public/data`):

- `<city>/new-listings.json` holds the snapshot from the publisher read in this run. The New listings page shows it. Read `source`, `asOf`, `partial` and `stale` before comparing snapshots. If a refresh fails, the file keeps the last successful snapshot with its original timestamp, marked stale.
- `sources/<provider>/<city>/new-listings.json` holds each publisher's last successful snapshot. These snapshots can have different ages.
- `<city>/listings.json` is the merged view. It is rebuilt every run from the latest snapshot of every publisher that was read in the last seven days. It has one row per home, keyed by a normalised street address with unit and ZIP code (`key`). Each row keeps the earliest list date, the most recently read price, the largest photo gallery, the longest description and any home facts. Its `sources` list has one entry per publisher, with that publisher's `url`, `source`, `date`, `price`, `photos` and `description`. Two URLs from the same publisher are never merged. Homes accumulate: every run starts from the previous file, so a home stays after its publisher's read stops listing it (each read covers only the newest pages). `firstSeen` and `lastSeen` record when a publisher's read first and last listed it; the New listings page marks a home not seen for 14 days as possibly off the market. A home is dropped once its list date is over 180 days old, or when its property page says it is no longer for sale (that URL is kept in `offMarket` so an older snapshot cannot bring it back). Homes listed over 30 days ago lose their photo URLs and per-publisher descriptions. The file lives in the same Actions cache as the harvest state, so the history is lost if that cache expires (7 days without a run).
- `<city>/harvest-state.json` holds the rotation and back-off state.

Listings are dated within seven city-local calendar days. Rows include the original property URL, address, list date, asking price, and a `photos` array of publisher-hosted image URLs. `description` holds text from the property page when available, and `summary` can hold a search-card summary. Where the publisher's page data includes them, rows also have `beds`, `baths`, `sqft`, `type` (the publisher's own label), `lat`, `lon` and `zip`. `detailAt` records when the property page was read. By default, each run reads up to 20 additional property pages per city for descriptions and full galleries (`--details`). Details already fetched for the same list date and price are reused, and refreshed after 12 hours.

The **Harvest live listings** Actions workflow runs on demand and on four schedules a day: 00:23, 12:23, 16:23 and 20:23 UTC. GitHub can delay scheduled runs. The Actions cache carries the output directory between runs. The downloadable `live-listings` artifact includes every snapshot, the merged files and the state. The daily site build copies the latest city snapshots. If the cache is empty, the build reads one publisher per city itself. A city that still has no snapshot shows the unavailable state.

#### Listing tags

```bash
python3 -m engine.listing_analysis --no-photos     # text signals for every city, no model
python3 -m engine.listing_analysis --dry-run       # the Philadelphia photo-tagging order, with reasons
python3 -m engine.listing_analysis --limit 10      # tag photos for up to 10 Philadelphia listings
```

`engine/listing_analysis.py` writes `<city>/listing-tags.json`. Text signals are phrase matches in the publisher’s description, such as “as-is”, “cash only”, “needs TLC”, “new roof 2024”, tenant-occupied, estate sale or “virtually staged”. The file also records how unusual a description is among the city’s other descriptions, and compares the asking price with the area’s recorded 12-month median.

Photo analysis covers Philadelphia only for now. It runs `claude -p` (Claude Code in print mode) on the CLI’s login, so it uses a Claude subscription and needs no API key. Up to 24 gallery photos per listing are read by the model with only the Read tool. The photos come from a private holding area (`engine/photo_hold.py`, default `photo-hold/`). A fetch step fills it ahead of tagging, in the tagging queue’s order. Interrupted downloads resume on the next run, and a 403 or 429 pauses that image host. Each listing’s held photos are deleted once it has been tagged, after 14 days untagged, 30 days after the list date, or when the listing disappears. The holding area is capped in size. In Actions it lives only in the `photo-hold-v1-` cache. A publish guard (`python -m engine.photo_hold --check`) fails the build if a held photo, or a publisher photo URL in the analysis store, could be published or committed. The model returns schema-checked tags: condition, finishes, room types, visible defects with photo, evidence and confidence, and possible editing. Python then works out which expected spaces for the home type are not shown, and rates coverage as good, partial or thin.

Photos can show a problem, but they cannot rule one out. A home counts as “no problems seen” only when the photos cover it well, and a thin gallery cannot score above 60. An edited-photo flag is either a label from the publisher, in a photo or in the description, or the model’s low-confidence impression. The site shows the second kind as “possibly edited”, never as a finding.

Repeat detections are grouped into findings, one defect type in one room. Several photos of the same stained ceiling count once, and the number of rooms is capped by the listing’s bedroom and bathroom counts. The grouping is approximate. The condition score subtracts up to 40 points from the model’s overall rating, using `1 − Π(1 − severity)` over findings, so each additional room adds less than the one before. The severity weights rank structural and water damage above surface damage, and surface damage above cosmetic issues and debris. They are priors, to be re-estimated once tags can be tested against sale-price residuals from `engine/model.py`.

Only small crops of detected defects are kept, at most 320 px, credited and linked to the publisher’s listing. Crops and photo tags are deleted 30 days after the list date. A text-only summary of the derived tags is kept longer in `tag-history/<city>.jsonl` for the sale-price backtest. It holds no images and no photo URLs, and `--no-tag-history` turns it off. EveryHome galleries are excluded from photo analysis. A home listed there is analysed with another publisher’s gallery when one exists. Its description still feeds the text signals. `--remove-crops-source` and `--remove-crops-url` delete crops at once for a removal request. Crops need Pillow or ImageMagick.

**Schedule.** The **Photo tags** workflow (`.github/workflows/photo-tags.yml`) runs at 00:41, 05:41, 10:41, 15:41 and 20:41 UTC. Each run tags up to 20 Philadelphia listings, distressed ones first, using the `CLAUDE_CODE_OAUTH_TOKEN` secret from `claude setup-token`. Plan usage counts against the same limits as your own Claude use. The workflow skips itself with a notice when the secret is missing. The analysis store, crops, usage ledger and tag history are kept under `listing-analysis/` on the `data` branch, and the daily **Data pipeline** publishes `listing-tags.json` and the crops with the site.

**Slow-down.** A run switches to slow mode at 60% of the 5-hour limit or 65% of the weekly limit. Slow mode tags at most 5 listings, chosen by `slow_mode_priority`: homes asking furthest below their estimate on the listing cards, then homes with distress wording and no estimate. `claude -p` does not document a way to read plan usage. The tagger reads rate-limit events from `--output-format stream-json` when the CLI emits them. Otherwise it uses its own ledger of the cost the CLI reports for each call, measured against stand-in budgets (`--budget-5h-usd`, `--budget-7d-usd`). That ledger counts only this tagger’s usage, not your own Claude use. When a limit is hit, the run stops, keeps its results and waits until the reported reset time (5 hours if none is given) before tagging again.

**Codex comparison.** With `--codex-share 0.2`, a fixed 20% of listings, chosen by a hash of the listing URL, are tagged by `codex exec` instead of Claude. Codex gets the same prompt, the same JSON schema and the same validation. Every stored result records its backend, model and prompt version. `--compare N` also tags N already-tagged listings with the other backend and keeps both results in `listing-analysis/comparisons/`, which is never published. `--compare-report` prints how often the two agree on condition, which defect types only one of them found, the score difference, and the time and failures for each backend. In Actions, Codex runs when a `CODEX_AUTH_JSON` secret or a `CODEX_API_KEY` secret is set. `CODEX_AUTH_JSON` holds the contents of `~/.codex/auth.json` from a ChatGPT-plan login made with `cli_auth_credentials_store = "file"`, and is used in preference. `CODEX_API_KEY` is an OpenAI API key, billed to the API. The login file exists only in a temporary `CODEX_HOME` during the run and is never cached, committed or printed. Codex may refresh its tokens during a run. The refreshed file is written back to the secret with `CODEX_SECRET_WRITER`, a fine-grained token with this repository’s “Secrets: read and write” permission. Without that token, the run warns that the refresh could not be saved. If the login stops working, the run continues on Claude alone and asks you to reseed `CODEX_AUTH_JSON`. OpenAI’s docs advise against this login mode in CI for public repositories. The workflow reduces the risk by running only on schedule or manual dispatch, never on pull requests, and one run at a time.

#### Listing cards

`engine/listing_cards.py` writes `<city>/listing-cards.json` for the New listings page: one card per home with the asking price, list date, publisher links, facts, map coordinates where the publisher gave them, its area (the boundary file's point-in-polygon lookup, else the recorded-sales placement used for tags), the area's recorded $/sq ft and median from 12 complete months, a headline comparison, plus and minus items (price-model drivers, area price trend and incidents against the city figure, asking $/sq ft against recorded sales, description signals, photo findings) and defect-crop paths. In Philadelphia a listing whose address matches a recorded sale is priced by the price model from the parcel's property record, using the coefficients `engine/model.py` saves in `model-predictor.json`, with a range from the model's backtest error for that price band; other listings are compared with the area's $/sq ft (not condos or co-ops) or, for the area's main home type, its median. Asking prices are only compared with recorded sales, never added to them. The area tier on each card is worked out in the browser from the page's weights. The file is rebuilt by `python -m engine build`/`run`, after each listings harvest and listing-tag run, and by the pipeline before each deploy (`python -m engine.listing_cards --listings harvested-listings`).

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

The data belongs to its publishers.
