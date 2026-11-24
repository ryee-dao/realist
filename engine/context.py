"""Context data that turns sale prices into something you can act on: reported crime, population and rents.

Crime is pulled already aggregated (the police APIs count incidents per ~500 m grid cell on their own
servers), population comes from 2020 Census block groups, asking rents from Zillow's public rent index by
ZIP code, and the rents tenants pay from the Census Bureau's American Community Survey by tract. None of it identifies a person. Assigning cells and block groups to areas happens in analytics.
"""
import calendar
import csv
import datetime as dt
import io
import json
import os

from . import characteristics
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


def zori_annual(months, series):
    """Calendar-year averages of one ZIP's monthly series, for years with all twelve months: "2023:1612;2024:1655"."""
    by_year = {}
    for m, v in zip(months, series):
        if v:
            by_year.setdefault(m[:4], []).append(v)
    return ";".join(f"{y}:{round(sum(v) / 12)}" for y, v in sorted(by_year.items()) if len(v) == 12)


def harvest_rents(ctx, city, report, cache):
    """Typical asking rent per ZIP code: the latest month, the same month a year earlier, and calendar-year averages
    (the averages let a Census survey figure, stated in one year's dollars, be brought forward to today)."""
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
        rents[row[col["RegionName"]].zfill(5)] = {"rent": round(latest[1]), "rent_prev": round(year_ago) if year_ago else "", "month": latest[0][:7],
                                                  "annual": zori_annual(months, [v for _, v in series])}
        report["kept"] += 1
    ctx.store.write_lookup(f"{city}_rents", rents, ["rent", "rent_prev", "month", "annual"])


# ---- rents tenants pay: American Community Survey 5-year estimates, by census tract ----------------------------
# Rent tables only (project rule: no demographic variables). B25064 median gross rent (rent plus utilities, renter
# homes paying cash rent), B25031 the same by bedrooms, B25003 tenure (renter-occupied homes, used as weights).
ACS_TABLES = {
    "B25064": {"E001": "rent", "M001": "rent_moe"},
    "B25003": {"E003": "renters", "M003": "renters_moe"},
    "B25031": {f"{k}00{i + 2}": f"b{i}{'' if k == 'E' else '_moe'}" for i in range(6) for k in ("E", "M")},
}
ACS_COLUMNS = ["year", "rent", "rent_moe", "open", "renters", "renters_moe"] + [f"b{i}{s}" for i in range(6) for s in ("", "_moe")]
ACS_API = "https://api.census.gov/data/{year}/acs/acs5"
ACS_SUMMARY_FILE = "https://www2.census.gov/programs-surveys/acs/summary_file/{year}/table-based-SF/data/5YRData/acsdt5y{year}-{table}.dat"
ACS_REFRESH_DAYS = 30        # the 5-year release comes out once a year; look for a new one at most monthly
OPEN_ENDED = -333333333      # margin code: the median falls in an open-ended bracket ($3,500 or more, or under $100)
NO_MARGIN = -555555555       # margin code: a controlled estimate, exact by construction


def acs_value(text):
    """An ACS cell as a number, or None for the Bureau's negative annotation codes (no sample, not computable...)."""
    v = to_float(text)
    return None if v is None or v < 0 else v


def acs_tract_record(cells):
    """One tract's lookup row from its cells, keyed like the Summary File ("B25064_E001": "1406", ...)."""
    rec = {}
    for table, fields in ACS_TABLES.items():
        for cell, name in fields.items():
            raw = cells.get(f"{table}_{cell}")
            if name.endswith("_moe") and to_float(raw) == NO_MARGIN:
                rec[name] = 0
            else:
                v = acs_value(raw)
                rec[name] = "" if v is None else round(v)
    rec["open"] = 1 if to_float(cells.get("B25064_M001")) == OPEN_ENDED and rec["rent"] != "" else ""
    if rec["open"]:
        rec["rent_moe"] = ""
    return rec


def parse_summary_file(text, prefixes):
    """Tract rows of one ACS table-based Summary File (pipe-separated, every geography in the country) whose
    state+county FIPS start with one of `prefixes`. Returns {tract GEOID (11 digits): {"B25064_E001": "1406", ...}}."""
    lines = iter(text.splitlines())
    head = next(lines).split("|")
    marks = tuple("1400000US" + p for p in prefixes)
    out = {}
    for line in lines:
        if not line.startswith(marks):
            continue
        row = line.split("|")
        out[row[0][9:]] = dict(zip(head[1:], row[1:]))
    return out


def parse_api_rows(rows):
    """Census API JSON (a header row, then data rows) for tracts, re-keyed to Summary File names: B25064_001E -> B25064_E001."""
    head = rows[0]
    out = {}
    for row in rows[1:]:
        rec = dict(zip(head, row))
        geoid = rec["state"] + rec["county"] + rec["tract"]
        cells = out.setdefault(geoid, {})
        for name, v in rec.items():
            if name[:1] == "B" and name[-1] in "EM" and "_" in name:
                table, cell = name.split("_")
                cells[f"{table}_{cell[-1]}{cell[:-1]}"] = v
    return out


def _acs_fips(city):
    return [s + c for s, c in COUNTIES[city]["fips"]]


def _acs_latest(ctx, key):
    """The newest 5-year release that answers, trying this year back to three years ago. Returns (year, version)."""
    for year in range(ctx.today.year, ctx.today.year - 4, -1):
        try:
            if key:
                ctx.client.get_json(ACS_API.format(year=year), {"get": "B25064_001E", "for": "state:42", "key": key})
                return year, f"api-{year}"
            h = ctx.client.head(ACS_SUMMARY_FILE.format(year=year, table="b25064"))
            return year, f"{year}|{h.get('last-modified') or h.get('etag') or ''}"
        except FetchError:
            continue
    raise FetchError("no ACS 5-year release answered")


def _acs_fetch(ctx, year, key, prefixes):
    """{tract GEOID: cells} for every wanted county: from the Census API with a key, else from the Summary File."""
    cells = {}
    if key:
        names = [f"{t}_{c[1:]}{c[0]}" for t, fields in ACS_TABLES.items() for c in fields]
        for fips in prefixes:
            rows = ctx.client.get_json(ACS_API.format(year=year), {"get": ",".join(names), "for": "tract:*",
                                                                    "in": f"state:{fips[:2]} county:{fips[2:]}", "key": key})
            for g, c in parse_api_rows(rows).items():
                cells.setdefault(g, {}).update(c)
        return cells
    for table in ACS_TABLES:
        text = ctx.client.get_bytes(ACS_SUMMARY_FILE.format(year=year, table=table.lower())).decode("utf-8")
        for g, c in parse_summary_file(text, prefixes).items():
            cells.setdefault(g, {}).update(c)
    return cells


def harvest_census_rents(ctx, city, report, cache):
    """What tenants pay, per census tract, from the latest ACS 5-year release.

    Without a CENSUS_API_KEY the Census API refuses data queries, so the engine reads the Bureau's table-based Summary
    File instead (the same estimates; about 80 MB for the three tables, read once per run for every city). The release
    changes once a year, so a held lookup is re-checked at most every 30 days and re-read only when the file changed.
    """
    name = f"{city}_census_rents"
    held = ctx.store.read_lookup(name)
    checked = ctx.state.get("census_rents_checked")
    if held and not ctx.full and checked and (ctx.today - dt.date.fromisoformat(checked)).days < ACS_REFRESH_DAYS:
        report["skipped"] = f"checked {checked}; the survey is released yearly"
        return
    key = os.environ.get("CENSUS_API_KEY") or None
    if "latest" not in cache:
        cache["latest"] = _acs_latest(ctx, key)
    year, version = cache["latest"]
    if held and not ctx.full and version == ctx.state.get("census_rents_version"):
        ctx.state["census_rents_checked"] = ctx.today.isoformat()
        report["skipped"] = f"{year - 4}–{year} survey unchanged"
        return
    if "cells" not in cache:
        wanted = [f for c in COUNTIES for f in _acs_fips(c)]
        cache["cells"] = _acs_fetch(ctx, year, key, wanted)
    mine = tuple(_acs_fips(city))
    rows = {}
    for geoid, cells in cache["cells"].items():
        if not geoid.startswith(mine):
            continue
        report["fetched"] += 1
        rec = acs_tract_record(cells)
        if not rec["renters"]:      # tracts with renters but no published median stay in, so coverage can be judged
            report["rejected"]["no renter homes"] = report["rejected"].get("no renter homes", 0) + 1
            continue
        rows[geoid] = {**rec, "year": year}
        report["kept"] += 1
    if not rows:
        raise FetchError("no tract rents for this city")
    ctx.store.write_lookup(name, rows, ACS_COLUMNS)
    ctx.state.update(census_rents_year=year, census_rents_version=version, census_rents_checked=ctx.today.isoformat())


# ---- HUD Small Area Fair Market Rents, by ZIP code and bedrooms ----------------------------------------------------
# A fair market rent is the 40th percentile of gross rent (rent plus utilities) paid by recent movers into
# standard-quality homes, set for a federal fiscal year. Not a median: see engine/rents.py for how it is used.
HUD_FMR = "https://www.huduser.gov/hudapi/public/fmr/data/{entity}"
HUD_TOKEN_ENV = "HUD_CENSUS_API_KEY"       # the HUD USER API bearer token (repository secret)
HUD_BEDS = {"efficiency": 0, "one-bedroom": 1, "two-bedroom": 2, "three-bedroom": 3, "four-bedroom": 4}
SAFMR_COLUMNS = ["year"] + [f"b{i}" for i in range(5)]


def _hud_beds(entry):
    rec = {}
    for k, v in entry.items():
        i = HUD_BEDS.get(str(k).strip().lower())
        if i is not None and to_float(v):
            rec[f"b{i}"] = round(to_float(v))
    return rec


def parse_safmr(payload):
    """One HUD FMR API answer for a county as (fiscal year, {zip: {"b0".."b4"}}, metro-wide {"b0".."b4"} or None).

    In Small Area FMR areas (`smallarea_status` "1"), `data.basicdata` is a list: a "MSA level" entry and one entry
    per ZIP code of the whole metro ("zip_code", "Efficiency", "One-Bedroom", ...). Elsewhere it is a single
    metro-wide entry, which cannot tell one area from another (New York answers this way). The fiscal year is
    `data.year`, or the entry's own `year`.
    """
    data = (payload or {}).get("data") or {}
    basic = data.get("basicdata")
    entries = basic if isinstance(basic, list) else [basic] if isinstance(basic, dict) else []
    year, zips, metro = str(data.get("year") or ""), {}, None
    for entry in entries:
        year = year or str(entry.get("year") or "")
        rec = _hud_beds(entry)
        z = str(entry.get("zip_code") or entry.get("zipcode") or "").strip()
        if not rec:
            continue
        if len(z) == 5 and z.isdigit():
            zips[z] = rec
        elif not z or "msa" in z.lower() or "metro" in z.lower():
            metro = rec
    return year or None, zips, metro


def harvest_safmr(ctx, city, report):
    """Small Area FMRs for the city's counties, re-read at most monthly (HUD publishes them once a fiscal year)."""
    token = os.environ.get(HUD_TOKEN_ENV)
    if not token:
        report["skipped"] = f"no HUD USER API token ({HUD_TOKEN_ENV} not set)"
        return
    name = f"{city}_safmr"
    checked = ctx.state.get("safmr_checked")
    if ctx.store.read_lookup(name) and not ctx.full and checked and (ctx.today - dt.date.fromisoformat(checked)).days < ACS_REFRESH_DAYS:
        report["skipped"] = f"checked {checked}; published once a fiscal year"
        return
    rows, years, metro_only = {}, set(), 0
    for state, county in COUNTIES[city]["fips"]:
        payload = ctx.client.get_json(HUD_FMR.format(entity=f"{state}{county}99999"), {}, headers={"Authorization": f"Bearer {token}"})
        year, zips, metro = parse_safmr(payload)
        report["fetched"] += len(zips) + (metro is not None)
        if metro and "metro" not in rows:       # kept for a city-level cross-check only, under the key "metro"
            rows["metro"] = {**metro, "year": year or ""}
        if not zips:
            metro_only += 1
        for z, rec in zips.items():
            rows[z] = {**rec, "year": year or ""}
            years.add(year)
            report["kept"] += 1
    if metro_only:
        report["rejected"]["county with a metro-wide FMR only"] = metro_only
    if not rows:
        raise FetchError("HUD returned no fair market rents for this city's counties")
    ctx.store.write_lookup(name, rows, SAFMR_COLUMNS)
    ctx.state.update(safmr_year=max((y for y in years if y), default=rows["metro"]["year"] if "metro" in rows else None),
                     safmr_checked=ctx.today.isoformat())


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
    """Run the context pulls for one city. A failure here is reported but never blocks the sales data."""
    jobs = [
        ("Reported crime incidents", CRIME[city]["publisher"], lambda r: harvest_crime(ctx, city, r)),
        ("Residents per block group", "U.S. Census Bureau, 2020 Census", lambda r: harvest_population(ctx, city, r)),
        ("Rent index by ZIP code", "Zillow Observed Rent Index", lambda r: harvest_rents(ctx, city, r, cache)),
        ("Rents tenants pay, by census tract", "U.S. Census Bureau, American Community Survey 5-year",
         lambda r: harvest_census_rents(ctx, city, r, cache.setdefault("census", {}))),
        ("Small Area Fair Market Rents by ZIP code", "U.S. Department of Housing and Urban Development (HUD USER)",
         lambda r: harvest_safmr(ctx, city, r)),
        ("Listings by ZIP code", "Zillow Research", lambda r: harvest_listings(ctx, city, r, cache.setdefault("listings", {}))),
        ("Neighborhood boundaries", "City open data (GeoJSON)", lambda r: harvest_boundaries(ctx, city, r)),
    ]
    # property characteristics and assessment history for the price-drivers model (each skipped while current)
    for name, dataset, fn in characteristics.CITIES.get(city, {}).get("jobs", []):
        jobs.append((name, dataset, lambda r, fn=fn: fn(ctx, r)))
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
