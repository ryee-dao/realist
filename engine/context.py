"""Context data that turns sale prices into something you can act on: reported crime, population and rents.

Crime is pulled already aggregated (the police APIs count incidents per ~500 m grid cell on their own
servers), population comes from 2020 Census block groups, and rents from Zillow's public rent index by
ZIP code. None of it identifies a person. Assigning cells and block groups to areas happens in analytics.
"""
import calendar
import csv
import datetime as dt
import io
import json
import os

from .http import FetchError
from .schema import to_float, to_int

CELL = 200            # grid cells are 1/200 of a degree, roughly 500 m
TIGER = "https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/tigerWMS_Census2020/MapServer/8/query"
ZORI = "https://files.zillowstatic.com/research/public_csvs/zori/Zip_zori_uc_sfrcondomfr_sm_month.csv"
# Zillow Research for-sale listing metrics by ZIP (homes and condos, raw monthly, not smoothed): field -> file id.
# Free for public use with attribution to Zillow. Asking-side data, kept apart from recorded sales everywhere.
LISTINGS = {"active": "invt_fs", "new": "new_listings", "price": "mlp", "cut": "perc_listings_price_cut"}
LISTINGS_MONTHS = 13      # the latest month, the same month a year earlier, and everything between (for a sparkline)


def listings_url(metric):
    return f"https://files.zillowstatic.com/research/public_csvs/{metric}/Zip_{metric}_uc_sfrcondo_month.csv"

# Offences counted, grouped the way the FBI's long-running "Part I" index does, so cities are comparable.
CRIME = {
    "philadelphia": {
        "kind": "carto", "url": "https://phl.carto.com/api/v2/sql", "table": "incidents_part1_part2",
        "date": "dispatch_date", "lat": "point_y", "lon": "point_x", "offence": "text_general_code",
        "violent": ["Homicide - Criminal", "Rape", "Robbery Firearm", "Robbery No Firearm",
                    "Aggravated Assault Firearm", "Aggravated Assault No Firearm"],
        "property": ["Burglary Residential", "Burglary Non-Residential", "Thefts", "Theft from Vehicle",
                     "Motor Vehicle Theft", "Arson"],
        "publisher": "Philadelphia Police Department", "link": "https://opendataphilly.org/datasets/crime-incidents/",
    },
    "nyc": {
        "kind": "socrata", "base": "https://data.cityofnewyork.us", "datasets": ["qgea-i56i", "5uac-w243"], "latest": "5uac-w243",
        "date": "cmplnt_fr_dt", "lat": "latitude", "lon": "longitude", "offence": "ofns_desc",
        "violent": ["MURDER & NON-NEGL. MANSLAUGHTER", "RAPE", "ROBBERY", "FELONY ASSAULT"],
        "property": ["BURGLARY", "GRAND LARCENY", "GRAND LARCENY OF MOTOR VEHICLE", "PETIT LARCENY", "ARSON"],
        "publisher": "New York City Police Department", "link": "https://data.cityofnewyork.us/d/5uac-w243",
    },
    "chicago": {
        "kind": "socrata", "base": "https://data.cityofchicago.org", "datasets": ["ijzp-q8t2"], "latest": "ijzp-q8t2",
        "date": "date", "lat": "latitude", "lon": "longitude", "offence": "fbi_code",
        "violent": ["01A", "02", "03", "04A", "04B"],
        "property": ["05", "06", "07", "09"],
        "publisher": "Chicago Police Department", "link": "https://data.cityofchicago.org/d/ijzp-q8t2",
    },
}

CITIES_BOUNDARIES = {}     # filled in by engine.cities (kept here to avoid a circular import)

# (state FIPS, county FIPS) for population, and how the rent file names the same counties
COUNTIES = {
    "philadelphia": {"fips": [("42", "101")], "rent": ("PA", ["Philadelphia County"])},
    "nyc": {"fips": [("36", "005"), ("36", "047"), ("36", "061"), ("36", "081"), ("36", "085")],
            "rent": ("NY", ["Bronx County", "Kings County", "New York County", "Queens County", "Richmond County"])},
    "chicago": {"fips": [("17", "031")], "rent": ("IL", ["Cook County"])},
}


def month_add(year, month, delta):
    idx = year * 12 + month - 1 + delta
    return idx // 12, idx % 12 + 1


def windows(max_date):
    """The latest 12 complete months of data, and the 12 before them, as [start, end) ISO dates."""
    d = dt.date.fromisoformat(max_date[:10])
    y, m = (d.year, d.month) if d.day == calendar.monthrange(d.year, d.month)[1] else month_add(d.year, d.month, -1)
    end = dt.date(*month_add(y, m, 1), 1)
    mid = dt.date(*month_add(y, m, -11), 1)
    start = dt.date(*month_add(y, m, -23), 1)
    return {"cur": (mid.isoformat(), end.isoformat()), "prev": (start.isoformat(), mid.isoformat()), "through": f"{y:04d}-{m:02d}"}


def _quoted(values):
    return ",".join("'" + v.replace("'", "''") + "'" for v in values)


def _crime_cells(client, spec, start, end):
    """Incident counts per grid cell and offence between two dates, counted by the source's own server."""
    offences = spec["violent"] + spec["property"]
    if spec["kind"] == "carto":
        sql = (f"select round({spec['lat']}::numeric*{CELL})/{CELL} la, round({spec['lon']}::numeric*{CELL})/{CELL} lo, "
               f"{spec['offence']} o, count(*) n from {spec['table']} where {spec['date']} >= '{start}' and {spec['date']} < '{end}' "
               f"and {spec['offence']} in ({_quoted(offences)}) and {spec['lat']} is not null group by 1,2,3")
        yield from client.get_json(spec["url"], {"q": sql}).get("rows", [])
        return
    for dataset in spec["datasets"]:
        offset = 0
        while True:
            q = (f"select round({spec['lat']}*{CELL},0)/{CELL} as la, round({spec['lon']}*{CELL},0)/{CELL} as lo, {spec['offence']} as o, count(*) as n "
                 f"where {spec['date']} >= '{start}' and {spec['date']} < '{end}' and {spec['offence']} in ({_quoted(offences)}) "
                 f"and {spec['lat']} is not null group by la, lo, o order by la, lo, o limit 50000 offset {offset}")
            rows = client.get_json(f"{spec['base']}/resource/{dataset}.json", {"$query": q})
            if not isinstance(rows, list):
                raise FetchError(f"{dataset}: unexpected response {str(rows)[:200]}")
            yield from rows
            if len(rows) < 50000:
                break
            offset += 50000


def _max_date(client, spec):
    if spec["kind"] == "carto":
        rows = client.get_json(spec["url"], {"q": f"select max({spec['date']}) d from {spec['table']}"})["rows"]
        return str(rows[0]["d"])
    rows = client.get_json(f"{spec['base']}/resource/{spec['latest']}.json", {"$query": f"select max({spec['date']}) as d"})
    return str(rows[0]["d"])


def harvest_crime(ctx, city, report):
    spec = CRIME[city]
    win = windows(_max_date(ctx.client, spec))
    cells = {}
    for period in ("cur", "prev"):
        for row in _crime_cells(ctx.client, spec, *win[period]):
            la, lo, n = to_float(row.get("la")), to_float(row.get("lo")), to_int(row.get("n")) or 0
            report["fetched"] += n
            if la is None or lo is None or not (24 < la < 50 and -125 < lo < -66):
                report["rejected"]["no usable location"] = report["rejected"].get("no usable location", 0) + n
                continue
            kind = "violent" if row.get("o") in spec["violent"] else "property"
            cell = cells.setdefault(f"{la:.3f},{lo:.3f}", {"violent_cur": 0, "property_cur": 0, "violent_prev": 0, "property_prev": 0})
            cell[f"{kind}_{period}"] += n
            report["kept"] += n
    ctx.store.write_lookup(f"{city}_crime", cells, ["violent_cur", "property_cur", "violent_prev", "property_prev"])
    ctx.state["crime_through"] = win["through"]


def harvest_population(ctx, city, report):
    """2020 Census residents per block group, with each block group's centre point. Fetched once."""
    if ctx.store.read_lookup(f"{city}_population") and not ctx.full:
        report["skipped"] = "2020 Census counts do not change"
        return
    groups = {}
    for state, county in COUNTIES[city]["fips"]:
        offset = 0
        while True:
            data = ctx.client.get_json(TIGER, {
                "where": f"STATE='{state}' AND COUNTY='{county}'", "outFields": "GEOID,POP100,INTPTLAT,INTPTLON",
                "returnGeometry": "false", "orderByFields": "GEOID", "resultOffset": offset, "resultRecordCount": 1000, "f": "json"})
            if "features" not in data:
                raise FetchError(f"census: unexpected response {str(data)[:200]}")
            for feat in data["features"]:
                a = feat["attributes"]
                report["fetched"] += 1
                if a.get("POP100"):
                    groups[a["GEOID"]] = {"lat": float(a["INTPTLAT"]), "lon": float(a["INTPTLON"]), "pop": a["POP100"]}
                    report["kept"] += 1
                else:
                    report["rejected"]["block group with no residents"] = report["rejected"].get("block group with no residents", 0) + 1
            if len(data["features"]) < 1000:
                break
            offset += 1000
    ctx.store.write_lookup(f"{city}_population", groups, ["lat", "lon", "pop"])


def harvest_rents(ctx, city, report, cache):
    """Typical asking rent per ZIP code: the latest month and the same month a year earlier."""
    if "rows" not in cache:      # one download serves every city in the run
        cache["rows"] = list(csv.reader(io.StringIO(ctx.client.get_bytes(ZORI).decode("utf-8"))))
    head, body = cache["rows"][0], cache["rows"][1:]
    col = {name: i for i, name in enumerate(head)}
    months = [h for h in head if h[:2] == "20" and len(h) == 10]
    state, counties = COUNTIES[city]["rent"]
    rents = {}
    for row in body:
        if row[col["State"]] != state or row[col["CountyName"]] not in counties:
            continue
        report["fetched"] += 1
        series = [(m, to_float(row[col[m]])) for m in months]
        latest = next(((m, v) for m, v in reversed(series) if v), None)
        if not latest:
            report["rejected"]["no rent published"] = report["rejected"].get("no rent published", 0) + 1
            continue
        at = months.index(latest[0])
        year_ago = series[at - 12][1] if at >= 12 else None
        rents[row[col["RegionName"]].zfill(5)] = {"rent": round(latest[1]), "rent_prev": round(year_ago) if year_ago else "", "month": latest[0][:7]}
        report["kept"] += 1
    ctx.store.write_lookup(f"{city}_rents", rents, ["rent", "rent_prev", "month"])


def parse_zip_months(text, wanted, months=LISTINGS_MONTHS):
    """One Zillow ZIP-by-month CSV, cut down to the ZIPs in the wanted {state: {county, ...}} and the latest months.

    Lines for other states are skipped before they are parsed. Returns (month labels as YYYY-MM, oldest first,
    ending at the latest month any wanted ZIP has a value for; {zip: (state, county, [value or None, ...])}).
    """
    lines = iter(text.splitlines())
    head = next(csv.reader([next(lines)]))
    col = {name: i for i, name in enumerate(head)}
    dated = [i for i, h in enumerate(head) if h[:2] == "20" and len(h) == 10]
    marks = tuple(f",{st}," for st in wanted)
    rows = {}
    for line in lines:
        if not any(m in line for m in marks):
            continue
        row = next(csv.reader([line]))
        st, county = row[col["State"]], row[col["CountyName"]]
        if county not in wanted.get(st, ()):
            continue
        rows[row[col["RegionName"]].zfill(5)] = (st, county, [to_float(row[i]) if i < len(row) else None for i in dated])
    last = max((j for _, _, vals in rows.values() for j, v in enumerate(vals) if v is not None), default=None)
    if last is None:
        return [], {}
    first = max(0, last - months + 1)
    labels = [head[i][:7] for i in dated[first:last + 1]]
    return labels, {z: (st, c, vals[first:last + 1]) for z, (st, c, vals) in rows.items()}


def listing_rows(files, state, counties):
    """Per-ZIP lookup rows for one city from the parsed metric files: latest value, a year earlier, and the series."""
    month = max((labels[-1] for labels, _ in files.values() if labels), default=None)
    out = {}
    for field, (labels, rows) in files.items():
        if not labels or labels[-1] != month:    # a file a month behind the others would mix months; leave it out
            continue
        for z, (st, county, vals) in rows.items():
            if st != state or county not in counties:
                continue
            fmt = (lambda v: round(v, 4)) if field == "cut" else (lambda v: round(v))
            rec = out.setdefault(z, {"month": month})
            rec[field] = fmt(vals[-1]) if vals[-1] is not None else ""
            rec[f"{field}_prev"] = fmt(vals[-13]) if len(vals) >= 13 and vals[-13] is not None else ""
            rec[f"{field}_s"] = ";".join("" if v is None else str(fmt(v)) for v in vals)
    return month, out


LISTING_COLUMNS = ["month"] + [f"{f}{s}" for f in LISTINGS for s in ("", "_prev", "_s")]


def harvest_listings(ctx, city, report, cache):
    """For-sale listings by ZIP code from Zillow Research: active inventory, new listings, median list price, share cut.

    Four monthly files (about 46 MB together) are downloaded once per run and shared by every city. A HEAD request
    per file comes first: when none has changed since the last pull and the lookup is held, nothing is downloaded.
    """
    name = f"{city}_listings"
    if "versions" not in cache:
        cache["versions"] = {}
        for field, metric in LISTINGS.items():
            h = ctx.client.head(listings_url(metric))
            cache["versions"][field] = h.get("etag") or h.get("last-modified") or ""
    version = "|".join(cache["versions"][f] for f in LISTINGS)
    if version and version == ctx.state.get("listings_version") and ctx.store.read_lookup(name) and not ctx.full:
        report["skipped"] = "unchanged since the last pull"
        return
    if "files" not in cache:
        wanted = {}
        for spec in COUNTIES.values():
            wanted.setdefault(spec["rent"][0], set()).update(spec["rent"][1])
        cache["files"] = {field: parse_zip_months(ctx.client.get_bytes(listings_url(metric)).decode("utf-8"), wanted)
                          for field, metric in LISTINGS.items()}
    state, counties = COUNTIES[city]["rent"]
    month, rows = listing_rows(cache["files"], state, counties)
    for z, rec in list(rows.items()):
        report["fetched"] += 1
        if rec.get("active") in (None, "") and rec.get("price") in (None, ""):
            report["rejected"]["no listing figures for the latest month"] = report["rejected"].get("no listing figures for the latest month", 0) + 1
            del rows[z]
            continue
        report["kept"] += 1
    if not rows:
        raise FetchError("no listing figures for this city's ZIP codes")
    ctx.store.write_lookup(name, rows, LISTING_COLUMNS)
    ctx.state["listings_version"] = version
    ctx.state["listings_month"] = month


def harvest_boundaries(ctx, city, report):
    """Neighbourhood outlines, for cities whose sales files do not name the neighbourhood."""
    spec = CITIES_BOUNDARIES.get(city)
    path = ctx.store.boundary_path(city)
    if not spec:
        report["skipped"] = "sales already carry a neighbourhood"
        return
    if os.path.exists(path) and not ctx.full:
        report["skipped"] = "boundaries already held"
        return
    body = ctx.client.get_bytes(spec["url"])
    features = json.loads(body)["features"]
    report["fetched"] = report["kept"] = len(features)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(body)


def harvest(ctx, city, cache):
    """Run the three context pulls for one city. A failure here is reported but never blocks the sales data."""
    jobs = [
        ("Reported crime incidents", CRIME[city]["publisher"], lambda r: harvest_crime(ctx, city, r)),
        ("Residents per block group", "U.S. Census Bureau, 2020 Census", lambda r: harvest_population(ctx, city, r)),
        ("Rent index by ZIP code", "Zillow Observed Rent Index", lambda r: harvest_rents(ctx, city, r, cache)),
        ("Listings by ZIP code", "Zillow Research", lambda r: harvest_listings(ctx, city, r, cache.setdefault("listings", {}))),
        ("Neighborhood boundaries", "City open data (GeoJSON)", lambda r: harvest_boundaries(ctx, city, r)),
    ]
    errors = []
    for name, dataset, job in jobs:
        report = {"name": name, "dataset": dataset, "fetched": 0, "kept": 0, "new": 0, "updated": 0, "unchanged": 0,
                  "rejected": {}, "skipped": None, "context": True}
        before = ctx.client.stats["requests"]
        try:
            job(report)
        except Exception as e:
            report["skipped"] = f"failed: {type(e).__name__}: {e}"[:200]
            errors.append(f"{name}: {e}")
        report["requests"] = ctx.client.stats["requests"] - before
        ctx.reports.append(report)
        ctx.log(f"  {name}: " + (f"skipped ({report['skipped']})" if report["skipped"] else
                                 f"read {report['fetched']:,}, kept {report['kept']:,}, {report['requests']} requests"))
    return errors
