import { Index } from '../lib/data'
import { num } from '../lib/format'
import { site } from '../site'

export default function About({ index }: { index: Index }) {
  const total = index.cities.reduce((t, c) => t + c.rows, 0)
  return (
    <>
      <div className="page-head">
        <div>
          <h1>About Realist</h1>
          <p className="muted">A home-sale analytics site that runs itself: {num(total)} recorded sales across {index.cities.map((c) => c.name).join(', ')}, refreshed every day with no server and no manual work.</p>
        </div>
      </div>

      <div className="grid-2">
        <section className="card">
          <header className="card-head"><div><h3>What it does</h3></div></header>
          <div className="prose">
            <p>Realist reads the property records that city and county governments publish, keeps the genuine home sales, and turns them into market statistics: median prices, price per square foot, sales volume, and how each neighbourhood is moving.</p>
            <p>It adds rents and police-reported incidents for each area, so a buyer or investor can go from what homes actually sold for to a shortlist of areas worth a closer look.</p>
            <ul>
              <li><a href={`#/${index.cities[0]?.id}/find`}>Find areas</a>: set a budget and what you care about, and get a ranked shortlist with the reasons.</li>
              <li><a href={`#/${index.cities[0]?.id}/areas`}>Areas</a>: every neighbourhood ranked and mapped.</li>
              <li><a href="#/">Market</a>: citywide prices and volume over time, by home type.</li>
              <li><a href={`#/${index.cities[0]?.id}/compare`}>Crime</a>: police-reported incidents by neighbourhood, next to sale prices.</li>
              <li><a href={`#/${index.cities[0]?.id}/sales`}>Recent sales</a>: individual sales, each compared with what is typical nearby.</li>
              <li><a href="#/pipeline">Data pipeline</a>: the latest run, its checks and where every number came from.</li>
            </ul>
          </div>
        </section>

        <section className="card">
          <header className="card-head"><div><h3>How it is built</h3></div></header>
          <div className="prose">
            <p>A scheduled job runs the engine once a day. The engine is plain Python with no dependencies:</p>
            <ul>
              <li><b>Harvest.</b> One adapter per city pulls from the official open-data API. Requests are paced, retried with backoff, and skipped when a source has not changed.</li>
              <li><b>Validate.</b> Every record is mapped to one shared schema. Nominal transfers, non-residential property and multi-property deeds are rejected, and each rejection is counted.</li>
              <li><b>Store.</b> Sales are kept as one compressed file per city per month in the same repository, so the full history is versioned and free to host.</li>
              <li><b>Analyse and gate.</b> Statistics are rebuilt, then checked for lost records, stale data and implausible prices. The site only updates if every check passes.</li>
            </ul>
            <p>The site itself is static (React and TypeScript, hand-built SVG charts) and is served by GitHub Pages. The whole thing costs nothing to run.</p>
          </div>
        </section>

        <section className="card">
          <header className="card-head"><div><h3>Limits worth knowing</h3></div></header>
          <div className="prose">
            <ul>
              <li>These are recorded sales, not listings. Public records lag the market by several weeks, so the newest months are left out until they fill in.</li>
              <li>"Market now" listing figures are asking-side data from Zillow Research by ZIP code, mapped onto areas by where their sales are. They are shown apart from recorded sales and never mixed into sale-price charts.</li>
              <li>Philadelphia condos are left out because the city's records do not identify condo sales consistently.</li>
              <li>A median describes the homes that happened to sell. A change can reflect a different mix of homes as well as a change in values.</li>
              <li>Floor area and home details come from assessor records and are missing for some property types, mostly condos and co-ops.</li>
              <li>Incident figures are what police recorded, per resident. Places with many visitors and few residents read high, and reporting varies from place to place.</li>
              <li>Rental yield is gross: a year of typical asking rent over the typical sale price, before taxes, insurance, repairs and vacancy.</li>
              <li>No demographic data is used anywhere. Areas are compared only on prices, rents, sales and reported incidents.</li>
              <li>Nothing here is an appraisal or financial advice.</li>
            </ul>
          </div>
        </section>

        <section className="card">
          <header className="card-head"><div><h3>Methods</h3></div></header>
          <div className="prose">
            <ul>
              <li><b>Price growth.</b> Where an area has enough homes that sold twice, growth comes from a repeat-sales index, which compares each home with itself and so is not thrown off by which homes happened to sell. Elsewhere it is the change in median.</li>
              <li><b>Median range.</b> Each area's median comes with a 95% range. The fewer the sales, the wider it is.</li>
              <li><b>Incident rates.</b> Rates are pulled toward the city rate where few people live, so a handful of incidents does not swing a small area.</li>
              <li><b>Incident change.</b> A change on last year is shown only when it is unlikely to be chance. Otherwise it reads "no clear change".</li>
              <li><b>Net yield.</b> A rule of thumb: half the gross yield, for taxes, insurance, repairs, vacancy and management.</li>
            </ul>
          </div>
        </section>

        <section className="card">
          <header className="card-head"><div><h3>Who built it</h3></div></header>
          <div className="prose">
            <p>Realist was designed and built by <a href={site.authorUrl} target="_blank" rel="noreferrer">{site.author}</a>. It began as a listing scraper with its own server and database, and was rebuilt around open data and a zero-cost automated pipeline.</p>
            <p>The same approach works for any business that copies data between systems by hand: collect it on a schedule, check it, and put it where people can use it.</p>
            <p>
              <a href={site.repoUrl} target="_blank" rel="noreferrer">Read the source on GitHub</a>
              {site.contactEmail && <> · Get in touch: <b>{site.contactEmail}</b></>}
            </p>
          </div>
        </section>
      </div>
    </>
  )
}
