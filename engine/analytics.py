"""Turns the harvest store into the small JSON files the site reads."""
import datetime as dt
import json
import math
import os
from collections import Counter, defaultdict

from .cities import CITIES
from .context import CRIME
from .geo import Locator
from .schema import COOP, TYPES, to_float, to_int
from . import stats

ALL = "All homes"
MIN_N = 5            # fewest sales for a monthly or quarterly median to be published
AREA_MIN_SALES = 12  # fewest sales in 12 months for an area to be listed
RECENT_DAYS = 180
RECENT_CAP = 12000
YOY_MIN_SALES = 20   # fewest sales in each 12-month window for an area's change in median to be published


def quantile(sorted_values, q):
    if not sorted_values:
        return None
    pos = (len(sorted_values) - 1) * q
    lo, hi = math.floor(pos), math.ceil(pos)
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (pos - lo)


def median(values):
    return quantile(sorted(values), 0.5)


def pct_change(now, before):
    if now is None or before in (None, 0):
        return None
    return round((now / before - 1) * 100, 1)


def month_range(first, last):
    y, m = int(first[:4]), int(first[5:7])
    out = []
    while f"{y:04d}-{m:02d}" <= last:
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def shift(month, delta):
    idx = int(month[:4]) * 12 + int(month[5:7]) - 1 + delta
    return f"{idx // 12:04d}-{idx % 12 + 1:02d}"


def quarter(month):
    return f"{month[:4]} Q{(int(month[5:7]) - 1) // 3 + 1}"


COMPLETE_YOY = 0.9      # a recent month counts as filled in once it holds this share of the same month a year earlier
COMPLETE_MEDIAN = 0.75  # ...or, with no year-earlier month, this share of the trailing 12-month median
COMPLETE_MAX_BACK = 6


def month_is_complete(counts, i):
    """Compare a month with the same month a year earlier, so a seasonal low is not mistaken for a gap."""
    if i >= 12 and counts[i - 12] > 0:
        return counts[i] >= COMPLETE_YOY * counts[i - 12]
    prior = sorted(counts[max(0, i - 12):i])
    return i <= 12 or not prior or counts[i] >= COMPLETE_MEDIAN * quantile(prior, 0.5)


def last_complete_month(months, counts):
    """Recent months fill in as deeds are recorded. Walk back from the newest past any that are still thin."""
    end = len(months) - 1
    for _ in range(COMPLETE_MAX_BACK):
        if end <= 0 or month_is_complete(counts, end):
            break
        end -= 1
    return end


def sales_by_month(months, counts, n=24):
    """Sales held per sale month for the latest n months, including the months still filling in."""
    return [{"month": m, "count": c} for m, c in zip(months[-n:], counts[-n:])]


def nice_step(value):
    if value <= 0:
        return 1
    exp = 10 ** math.floor(math.log10(value))
    for mult in (1, 2, 2.5, 5, 10):
        if value <= mult * exp:
            return int(mult * exp)
    return int(10 * exp)


def load(store, city):
    cfg = CITIES[city]
    rows, coords = [], {}
    for r in store.iter_rows(city):
        price, sqft = int(r["price"]), to_int(r["sqft"])
        lat, lon = to_float(r["lat"]), to_float(r["lon"])
        if lat is not None and r["parcel"]:
            coords[r["parcel"]] = (lat, lon)
        if cfg["area_by"] == "zip":
            key = r["zip"]
        else:
            key = f"{r['area']}|{r['group']}" if r["area"] else ""
        ppsf = price / sqft if sqft else None
        if ppsf is not None and not (10 <= ppsf <= 5000):
            ppsf = None
        rows.append({
            "id": r["id"], "date": r["date"], "month": r["date"][:7], "price": price, "type": r["type"],
            "address": r["address"], "zip": r["zip"], "key": key, "sqft": sqft, "ppsf": ppsf,
            "yb": to_int(r["year_built"]), "beds": to_int(r["beds"]), "lat": lat, "lon": lon,
            "assessed": to_int(r["assessed"]), "parcel": r["parcel"],
        })
    if cfg.get("prepare"):   # city-specific record rules (e.g. hand-over between two sources of the same sales)
        rows = cfg["prepare"](rows)
    for r in rows:   # a parcel sold before keeps its known location
        if r["lat"] is None and r["parcel"] in coords:
            r["lat"], r["lon"] = coords[r["parcel"]]
    if cfg.get("boundaries") and os.path.exists(store.boundary_path(city)):
        place_in_boundaries(store, city, cfg, rows)
    return rows


def place_in_boundaries(store, city, cfg, rows):
    """Give each sale its neighbourhood from the boundary file. Answers are cached per parcel, so only new homes are worked out."""
    cache = store.read_lookup(f"{city}_neighborhood")
    locator, added = None, 0
    for r in rows:
        ref = r["parcel"] or f"{r['lat']},{r['lon']}"
        hit = cache.get(ref)
        if hit is None and r["lat"] is not None:
            locator = locator or Locator(store.boundary_path(city), cfg["boundaries"]["name"])
            hit = cache[ref] = {"area": locator.find(r["lat"], r["lon"]) or ""}
            added += 1
        r["key"] = f"{hit['area']}|" if hit and hit["area"] else ""
    if added:
        store.write_lookup(f"{city}_neighborhood", cache, ["area"])


def km(a, b):
    """Distance between two (lat, lon) points; flat-earth is accurate enough within a city."""
    dy = (a[0] - b[0]) * 111.2
    dx = (a[1] - b[1]) * 111.2 * math.cos(math.radians((a[0] + b[0]) / 2))
    return math.hypot(dx, dy)


MIN_RESIDENTS = 2000   # below this a per-resident rate says more about visitors than residents
NET_SHARE = 0.5        # the investors' "50% rule": about half of rent goes on taxes, insurance, repairs, vacancy and management

# repeat-sales index
RS_MIN_MONTHS = 12          # pairs closer together than this are mostly flips
RS_MAX_LOG_RATE = math.log(1.4)   # more than ~40% a year up (or the mirror image down) is a renovation, distress or an error
RS_MAX_SALES = 5            # a "home" that sold more often than this in the history is almost always a multi-unit record
RS_MIN_PAIRS = 100          # FHFA-style minimums for publishing an area's index
RS_MIN_HALF = 25


def repeat_pairs(rows, through):
    """Pairs of consecutive sales of the same home, as (period of first sale, period of second, log price ratio, area key).

    Periods are 12-month windows counted back from `through`, oldest first. Co-op sales are left out (the parcel is the
    whole building), as are parcels whose sales name more than one unit, sell too often, or sell twice within six
    months at different prices (several units recorded on one parcel).
    """
    end = int(through[:4]) * 12 + int(through[5:7]) - 1
    by_parcel = defaultdict(list)
    first_month = None
    for r in rows:
        mi = int(r["month"][:4]) * 12 + int(r["month"][5:7]) - 1
        if mi > end:
            continue
        first_month = mi if first_month is None else min(first_month, mi)
        if r["parcel"] and r["type"] != COOP:
            by_parcel[r["parcel"]].append((r["date"], mi, r["price"], r["key"], r["address"]))
    if first_month is None:
        return [], 0
    span = end - first_month + 1
    periods = span // 12 + (1 if span % 12 >= 6 else 0)   # keep an oldest part-year only if it has at least six months
    pairs = []
    for sales in by_parcel.values():
        if len(sales) < 2:
            continue
        sales.sort()
        clean = []
        for s in sales:     # the same deed recorded twice, or re-recorded at the same price, is one sale
            if clean and s[2] == clean[-1][2] and s[1] - clean[-1][1] <= 3:
                continue
            clean.append(s)
        if len(clean) < 2 or len(clean) > RS_MAX_SALES:
            continue
        if len({_unit(s[4]) for s in clean}) > 1:
            continue
        if any(b[1] - a[1] < 6 for a, b in zip(clean, clean[1:])):
            continue
        for a, b in zip(clean, clean[1:]):
            gap = b[1] - a[1]
            if gap < RS_MIN_MONTHS:
                continue
            y = math.log(b[2] / a[2])
            if abs(y) / (gap / 12) > RS_MAX_LOG_RATE:
                continue
            t1, t2 = periods - 1 - (end - a[1]) // 12, periods - 1 - (end - b[1]) // 12
            if t1 < 0:
                continue
            pairs.append((t1, t2, y, b[3]))
    return pairs, periods


def _unit(address):
    a = address.lower()
    for sep in ("#", ","):
        if sep in a:
            return "".join(ch for ch in a.rsplit(sep, 1)[1] if ch.isalnum())
    return ""


def repeat_growth(pairs, periods, min_pairs=RS_MIN_PAIRS, min_half=RS_MIN_HALF):
    """Index changes over the latest year and five years (in %), or None where the data fall short of the minimums."""
    levels, half = stats.repeat_sales_index([p[:3] for p in pairs], periods)
    last = periods - 1

    def change(back):
        then = last - back
        if len(pairs) < min_pairs or then < 0 or half[last] < min_half or half[then] < min_half:
            return None
        if levels[last] is None or levels[then] is None:
            return None
        return round((levels[last] / levels[then] - 1) * 100, 1)

    return change(1), change(5), levels, half


LISTINGS_MIN_COVERAGE = 0.6   # share of an area's sales that must lie in ZIP codes with listing figures
LISTINGS_YOY_COVERAGE = 0.8   # share of that covered weight that must have a year-earlier figure too
LISTING_FIELDS = (("active", "active", True), ("new", "newListings", True), ("price", "listPrice", False), ("cut", "priceCutShare", False))


def _num(v):
    return float(v) if v not in (None, "") else None


def listing_figures(zips, listings, zip_sales=None):
    """Zillow's per-ZIP listing figures mapped onto one area, weighted by where the area's recent sales are.

    `zips` counts the area's last-12-month sales per ZIP. Counts (active and new listings) are apportioned: each ZIP
    contributes its count times the share of that ZIP's sales (`zip_sales`, citywide) that fall in this area; with no
    `zip_sales` each ZIP counts in full, which is right for the whole city. Prices and shares are sales-weighted means.
    A figure is left out when under 60% of the area's sales lie in ZIPs that have it; the change on a year is worked
    out over the ZIPs that have both months, and only when they hold 80% of that weight. The price-cut share is a
    percentage and its change is in percentage points.
    """
    total = sum(zips.values())
    if not total or not listings:
        return None
    have_any = sum(n for z, n in zips.items() if z in listings and _num(listings[z].get("active")) is not None)
    if have_any / total < LISTINGS_MIN_COVERAGE:
        return None
    out = {"month": next(listings[z]["month"] for z in zips if z in listings), "coverage": round(have_any / total * 100)}

    def level(field, pool, key, apportion):
        if apportion:
            return sum(_num(listings[z][key]) * (n / zip_sales[z] if zip_sales else 1) for z, n in pool.items())
        return sum(_num(listings[z][key]) * n for z, n in pool.items()) / sum(pool.values())

    for field, name, apportion in LISTING_FIELDS:
        have = {z: n for z, n in zips.items() if z in listings and _num(listings[z].get(field)) is not None}
        if sum(have.values()) / total < LISTINGS_MIN_COVERAGE:
            out[name] = out[f"{name}YoY"] = None
            continue
        now = level(field, have, field, apportion)
        both = {z: n for z, n in have.items() if _num(listings[z].get(f"{field}_prev")) is not None}
        yoy = None
        if both and sum(both.values()) / sum(have.values()) >= LISTINGS_YOY_COVERAGE:
            a, b = level(field, both, field, apportion), level(field, both, f"{field}_prev", apportion)
            yoy = round((a - b) * 100, 1) if field == "cut" else pct_change(a, b)
        out[name] = round(now * 100, 1) if field == "cut" else round(now)
        out[f"{name}YoY"] = yoy
    return out


def listing_series(zips, listings):
    """Citywide monthly series for a sparkline: active listings summed over ZIPs with a full series, list price sales-weighted."""
    def series(z, field):
        return [_num(v) for v in (listings[z].get(f"{field}_s") or "").split(";")]

    pool = {z: n for z, n in zips.items() if z in listings and listings[z].get("active_s")}
    if not pool:
        return None
    length = max(len(series(z, "active")) for z in pool)
    full = [series(z, "active") for z in pool]
    full = [v for v in full if len(v) == length and None not in v]
    price = []
    for i in range(length):
        pts = [(v[i], n) for v, n in ((series(z, "price"), n) for z, n in pool.items()) if len(v) == length and v[i] is not None]
        price.append(round(sum(v * n for v, n in pts) / sum(n for _, n in pts)) if pts else None)
    end = next(iter(listings.values()))["month"]
    return {"months": [shift(end, i - length + 1) for i in range(length)],
            "active": [round(sum(v[i] for v in full)) for i in range(length)] if full else None, "listPrice": price}


def add_context(store, city, areas, geo, last12, city_median):
    """Attach reported crime, residents and rent to each area.

    Areas have no official boundaries in the sales data, so each area's catchment is everything nearer to its
    centre (the middle of its recent sales) than to any other area's, within reach of where its sales happen.
    Crime counts and residents use the same catchments, so the rate between them is consistent.
    """
    centres = [(a["key"], geo[a["key"]]) for a in areas if geo[a["key"]]["centre"]]

    def nearest(point):
        best, best_d = None, 1e9
        for key, g in centres:
            d = km(point, g["centre"])
            if d < best_d:
                best, best_d = key, d
        return best if best and best_d <= max(1.0, 1.6 * geo[best]["radius"]) else None

    tally = {a["key"]: {"pop": 0, "violent_cur": 0, "property_cur": 0, "violent_prev": 0, "property_prev": 0} for a in areas}
    for g in store.read_lookup(f"{city}_population").values():
        key = nearest((float(g["lat"]), float(g["lon"])))
        if key:
            tally[key]["pop"] += int(g["pop"])
    crime = store.read_lookup(f"{city}_crime")
    for cell, c in crime.items():
        la, lo = cell.split(",")
        key = nearest((float(la), float(lo)))
        if key:
            for k in ("violent_cur", "property_cur", "violent_prev", "property_prev"):
                tally[key][k] += int(c[k])
    rents = store.read_lookup(f"{city}_rents")

    def rent_for(zips):
        total = sum(zips.values())
        have = {z: n for z, n in zips.items() if z in rents}
        if not total or sum(have.values()) / total < 0.5:
            return None, None
        w = sum(have.values())
        now = sum(int(rents[z]["rent"]) * n for z, n in have.items()) / w
        both = {z: n for z, n in have.items() if rents[z]["rent_prev"]}
        yoy = None
        if both and sum(both.values()) / w >= 0.8:
            yoy = pct_change(sum(int(rents[z]["rent"]) * n for z, n in both.items()), sum(int(rents[z]["rent_prev"]) * n for z, n in both.items()))
        return round(now), yoy

    listings = store.read_lookup(f"{city}_listings")
    zip_sales = Counter(r["zip"] for r in last12 if r["zip"])
    add_context.nearest = nearest
    rated = [a for a in areas if crime and tally[a["key"]]["pop"] >= MIN_RESIDENTS]
    # Small catchments give noisy rates: shrink each toward the rate across all rated areas (global empirical Bayes)
    shrunk = {}
    for kind in ("violent", "property"):
        counts = [tally[a["key"]][f"{kind}_cur"] for a in rated]
        pops = [tally[a["key"]]["pop"] for a in rated]
        est, _, _, _ = stats.eb_rates(counts, pops)
        shrunk[kind] = {a["key"]: v for a, v in zip(rated, est)}
    rated_keys = {a["key"] for a in rated}
    crime_areas = rent_areas = listing_areas = 0
    for a in areas:
        t = tally[a["key"]]
        ok = a["key"] in rated_keys
        a["residents"] = t["pop"] or None
        for kind in ("violent", "property"):
            a[f"{kind}Rate"] = round(shrunk[kind][a["key"]] * 1000, 1) if ok else None
            a[f"{kind}Raw"] = round(t[f"{kind}_cur"] / t["pop"] * 1000, 1) if ok else None
        if ok:
            a["crimeTrend"], a["crimeYoY"] = stats.count_trend(t["violent_cur"] + t["property_cur"], t["violent_prev"] + t["property_prev"])
        else:
            a["crimeTrend"], a["crimeYoY"] = None, None
        a["rent"], a["rentYoY"] = rent_for(geo[a["key"]]["zips"])
        a["yield"] = round(a["rent"] * 12 / a["median12"] * 100, 1) if a["rent"] and a["median12"] else None
        a["netYield"] = round(a["yield"] * NET_SHARE, 1) if a["yield"] is not None else None
        crime_areas += a["violentRate"] is not None
        rent_areas += a["rent"] is not None
        a["listings"] = listing_figures(geo[a["key"]]["zips"], listings, zip_sales)
        listing_areas += a["listings"] is not None

    city_listings = listing_figures(zip_sales, listings)
    if city_listings:
        city_listings["series"] = listing_series(zip_sales, listings)
    pop =sum(t["pop"] for t in tally.values())
    city_rent, city_rent_yoy = rent_for(Counter(r["zip"] for r in last12 if r["zip"]))
    state = store.read_state().get(city, {})
    city_trend, city_yoy = stats.count_trend(sum(t["violent_cur"] + t["property_cur"] for t in tally.values()),
                                             sum(t["violent_prev"] + t["property_prev"] for t in tally.values())) if crime else (None, None)
    return {
        "crimeAreas": crime_areas, "rentAreas": rent_areas, "residents": pop or None,
        "crimeThrough": state.get("crime_through") if crime else None,
        "violentRate": round(sum(t["violent_cur"] for t in tally.values()) / pop * 1000, 1) if pop and crime else None,
        "propertyRate": round(sum(t["property_cur"] for t in tally.values()) / pop * 1000, 1) if pop and crime else None,
        "crimeYoY": city_yoy, "crimeTrend": city_trend,
        "rent": city_rent, "rentYoY": city_rent_yoy,
        "rentMonth": next(iter(rents.values()))["month"] if rents else None,
        "yield": round(city_rent * 12 / city_median * 100, 1) if city_rent and city_median else None,
        "listingAreas": listing_areas, "listings": city_listings,   # build_city moves "listings" into overview.json
    }


def build_grid(store, city, rows, since_month, nearest, area_index):
    """A ~500 m grid for the compare map: reported incidents and typical sale price in each cell."""
    cells = {}
    for key, c in store.read_lookup(f"{city}_crime").items():
        cells[key] = {"v": int(c["violent_cur"]), "p": int(c["property_cur"]), "b": int(c["violent_prev"]) + int(c["property_prev"])}
    prices = defaultdict(list)
    for r in rows:
        if r["lat"] is not None and r["month"] >= since_month:
            prices[f"{round(r['lat'] * 200) / 200:.3f},{round(r['lon'] * 200) / 200:.3f}"].append(r["price"])
    for key, values in prices.items():
        if len(values) >= 4:
            cells.setdefault(key, {"v": 0, "p": 0, "b": 0}).update(m=round(median(values)), n=len(values))
    out = {k: [] for k in ("lat", "lon", "violent", "property", "before", "price", "sales", "area")}
    for key in sorted(cells):
        c = cells[key]
        la, lo = key.split(",")
        out["lat"].append(float(la)); out["lon"].append(float(lo))
        out["violent"].append(c["v"]); out["property"].append(c["p"]); out["before"].append(c["b"])
        out["price"].append(c.get("m")); out["sales"].append(c.get("n", 0))
        out["area"].append(area_index.get(nearest((float(la), float(lo))), -1))
    return out


def area_label(cfg, key):
    if cfg["area_by"] == "zip":
        return cfg["area_names"].get(key, f"ZIP {key}"), key
    name, _, group = key.partition("|")
    return name, group


def window_stats(rows):
    prices = sorted(r["price"] for r in rows)
    ppsf = sorted(r["ppsf"] for r in rows if r["ppsf"])
    return {
        "n": len(prices),
        "median": round(quantile(prices, 0.5)) if len(prices) >= MIN_N else None,
        "ppsf": round(quantile(ppsf, 0.5)) if len(ppsf) >= MIN_N else None,
    }


def build_city(store, city, out_dir):
    cfg = CITIES[city]
    rows = load(store, city)
    if not rows:
        return None
    rows.sort(key=lambda r: r["date"])
    latest = rows[-1]["date"]
    months = month_range(rows[0]["month"], latest[:7])
    midx = {m: i for i, m in enumerate(months)}
    counts = [0] * len(months)
    for r in rows:
        counts[midx[r["month"]]] += 1
    end = last_complete_month(months, counts)
    recent_months = sales_by_month(months, counts)
    months = months[:end + 1]
    through = months[-1]
    cur3, prev3 = (shift(through, -2), through), (shift(through, -14), shift(through, -12))
    cur12, prev12 = (shift(through, -11), through), (shift(through, -23), shift(through, -12))
    old12 = (shift(through, -71), shift(through, -60))

    def within(r, win):
        return win[0] <= r["month"] <= win[1]

    # ---- citywide series and headline numbers, per home type ----
    present = [t for t in TYPES if any(r["type"] == t for r in rows)]
    overview_types = {}
    for label in [ALL] + present:
        subset = [r for r in rows if label == ALL or r["type"] == label]
        by_month = defaultdict(list)
        for r in subset:
            by_month[r["month"]].append(r)
        series = {k: [] for k in ("median", "p25", "p75", "count", "ppsf")}
        for m in months:
            group = by_month.get(m, [])
            prices = sorted(r["price"] for r in group)
            ppsf = sorted(r["ppsf"] for r in group if r["ppsf"])
            ok = len(prices) >= MIN_N
            series["count"].append(len(prices))
            series["median"].append(round(quantile(prices, 0.5)) if ok else None)
            series["p25"].append(round(quantile(prices, 0.25)) if ok else None)
            series["p75"].append(round(quantile(prices, 0.75)) if ok else None)
            series["ppsf"].append(round(quantile(ppsf, 0.5)) if len(ppsf) >= MIN_N else None)
        now3, was3 = window_stats([r for r in subset if within(r, cur3)]), window_stats([r for r in subset if within(r, prev3)])
        last12 = [r for r in subset if within(r, cur12)]
        now12, was12 = window_stats(last12), window_stats([r for r in subset if within(r, prev12)])
        prices12 = sorted(r["price"] for r in last12)
        step = nice_step((quantile(prices12, 0.95) or 1) / 14)
        edges = [step * i for i in range(16)]
        hist = [0] * 16
        for p in prices12:
            hist[min(15, p // step)] += 1
        overview_types[label] = {
            "series": series,
            "kpi": {
                # a year-on-year figure needs enough sales on both sides to mean anything
                "median": now3["median"], "medianYoY": pct_change(now3["median"], was3["median"]) if min(now3["n"], was3["n"]) >= 30 else None,
                "ppsf": now3["ppsf"], "ppsfYoY": pct_change(now3["ppsf"], was3["ppsf"]) if min(now3["n"], was3["n"]) >= 30 else None,
                "sales12": now12["n"], "salesYoY": pct_change(now12["n"], was12["n"]),
                "median12": now12["median"],
            },
            "hist": {"edges": edges, "counts": hist},
        }

    # ---- areas ----
    by_area = defaultdict(list)
    for r in rows:
        if r["key"]:
            by_area[r["key"]].append(r)
    quarters = sorted({quarter(m) for m in months if m[:4] >= str(int(through[:4]) - 6)})
    # only quarters fully inside the complete range
    quarters = [q for q in quarters if shift(f"{q[:4]}-{int(q[-1]) * 3:02d}", 0) <= through]
    # ---- repeat sales: pairs built once for the city, then grouped by area ----
    pairs, periods = repeat_pairs(rows, through)
    pairs_by_area = defaultdict(list)
    for p in pairs:
        if p[3]:
            pairs_by_area[p[3]].append(p)
    city_g1, city_g5, city_levels, city_half = repeat_growth(pairs, periods)
    repeat_index = None
    if any(v is not None for v in city_levels):
        first = next(i for i, v in enumerate(city_levels) if v is not None)
        repeat_index = {
            "years": [shift(through, -12 * (periods - 1 - t)) for t in range(first, periods)],   # end month of each 12-month period
            "index": [round(v / city_levels[first] * 100, 1) if v is not None else None for v in city_levels[first:]],
            "halfPairs": city_half[first:], "pairs": len(pairs), "growth1": city_g1, "growth5": city_g5,
        }

    areas, area_medians, area_index, geo = [], {}, {}, {}
    for key, group in by_area.items():
        last12 = [r for r in group if within(r, cur12)]
        if len(last12) < AREA_MIN_SALES:
            continue
        now12, was12 = window_stats(last12), window_stats([r for r in group if within(r, prev12)])
        was5 = window_stats([r for r in group if within(r, old12)])
        by_q = defaultdict(list)
        for r in group:
            by_q[quarter(r["month"])].append(r["price"])
        located = [r for r in group if r["lat"] is not None and r["month"] >= shift(through, -35)]
        name, sub = area_label(cfg, key)
        mix = Counter(r["type"] for r in last12)
        for t in present:   # medians per type for the "vs area" signal on each sale
            ws = window_stats([r for r in last12 if r["type"] == t])
            if ws["n"] >= 8:
                area_medians[(key, t)] = ws
        centre = (median([r["lat"] for r in located]), median([r["lon"] for r in located])) if located else None
        spread = sorted(km(centre, (r["lat"], r["lon"])) for r in located) if located else []
        geo[key] = {"centre": centre, "radius": max(0.6, quantile(spread, 0.9) or 0), "zips": Counter(r["zip"] for r in last12 if r["zip"])}
        area_index[key] = len(areas)
        low, high = stats.median_ci(sorted(r["price"] for r in last12)) if now12["median"] is not None else (None, None)
        median_yoy = pct_change(now12["median"], was12["median"]) if min(now12["n"], was12["n"]) >= YOY_MIN_SALES else None
        change5y = pct_change(now12["median"], was5["median"]) if was5["n"] >= 10 else None
        area_pairs = pairs_by_area.get(key, [])
        g1, g5, _, _ = repeat_growth(area_pairs, periods) if len(area_pairs) >= RS_MIN_PAIRS else (None, None, None, None)
        repeat = g1 is not None
        areas.append({
            "key": key, "name": name, "group": sub,
            "lat": round(median([r["lat"] for r in located]), 4) if located else None,
            "lon": round(median([r["lon"] for r in located]), 4) if located else None,
            "sales12": now12["n"], "salesYoY": pct_change(now12["n"], was12["n"]) if was12["n"] >= 10 else None,
            "median12": now12["median"],
            "medianLow": low, "medianHigh": high,
            "medianYoY": median_yoy,
            "ppsf12": now12["ppsf"],
            "change5y": change5y,
            "growthMethod": "repeat" if repeat else "median", "repeatPairs": len(area_pairs),
            "priceGrowth1": g1 if repeat else median_yoy,
            "priceGrowth5": (g5 if repeat else change5y),
            "zips": [z for z, _ in geo[key]["zips"].most_common(2)],
            "topType": mix.most_common(1)[0][0], "topTypeShare": round(mix.most_common(1)[0][1] / len(last12) * 100),
            "quarterly": [round(median(by_q[q])) if len(by_q.get(q, [])) >= MIN_N else None for q in quarters],
        })
    context = add_context(store, city, areas, geo, [r for r in rows if within(r, cur12)], overview_types[ALL]["kpi"]["median12"])
    areas.sort(key=lambda a: -a["sales12"])
    area_index = {a["key"]: i for i, a in enumerate(areas)}

    # ---- recent individual sales ----
    cutoff = (dt.date.fromisoformat(latest) - dt.timedelta(days=RECENT_DAYS)).isoformat()
    recent = [r for r in rows if r["date"] >= cutoff][-RECENT_CAP:][::-1]
    cols = {k: [] for k in ("date", "price", "type", "address", "zip", "area", "sqft", "yb", "beds", "lat", "lon", "vs", "basis", "ratio")}
    for r in recent:
        ref = area_medians.get((r["key"], r["type"]))
        vs = basis = None
        if ref and r["ppsf"] and ref["ppsf"]:
            vs, basis = round((r["ppsf"] / ref["ppsf"] - 1) * 100), 1
        elif ref and ref["median"]:
            vs, basis = round((r["price"] / ref["median"] - 1) * 100), 0
        cols["date"].append(r["date"])
        cols["price"].append(r["price"])
        cols["type"].append(TYPES.index(r["type"]))
        cols["address"].append(r["address"])
        cols["zip"].append(r["zip"])
        cols["area"].append(area_index.get(r["key"], -1))
        cols["sqft"].append(r["sqft"])
        cols["yb"].append(r["yb"])
        cols["beds"].append(r["beds"])
        cols["lat"].append(round(r["lat"], 4) if r["lat"] is not None else None)
        cols["lon"].append(round(r["lon"], 4) if r["lon"] is not None else None)
        cols["vs"].append(vs)
        cols["basis"].append(basis)
        cols["ratio"].append(round(r["price"] / r["assessed"] * 100) if r["assessed"] and r["assessed"] >= 10000 else None)
    if not any(v is not None for v in cols["ratio"]):
        del cols["ratio"]
    if not any(v is not None for v in cols["beds"]):
        del cols["beds"]

    os.makedirs(os.path.join(out_dir, city), exist_ok=True)
    meta = {"city": city, "name": cfg["name"], "through": through, "latestSale": latest}
    _dump(os.path.join(out_dir, city, "overview.json"),
          {**meta, "months": months, "typeOrder": [ALL] + present, "types": overview_types, "repeatIndex": repeat_index,
           "listings": context.pop("listings", None)})
    _dump(os.path.join(out_dir, city, "areas.json"), {**meta, "quarters": quarters, "areas": areas})
    _dump(os.path.join(out_dir, city, "sales.json"),
          {**meta, "from": recent[-1]["date"] if recent else None, "typeNames": TYPES, "count": len(recent), "columns": cols})

    _dump(os.path.join(out_dir, city, "grid.json"), {**meta, "cell": 0.005, "crimeThrough": context["crimeThrough"],
                                                      "columns": build_grid(store, city, rows, shift(through, -23), add_context.nearest, area_index)})

    # ---- facts for quality checks and the index ----
    last12 = [r for r in rows if within(r, cur12)]
    n12 = max(1, len(last12))
    all_kpi = overview_types[ALL]["kpi"]
    spark = overview_types[ALL]["series"]["median"][-36:]
    return {
        "city": city, "rows": len(rows), "firstSale": rows[0]["date"], "latestSale": latest, "through": through,
        "areas": len(areas), "kpi": all_kpi, "spark": spark, "context": context, "byMonth": recent_months,
        "coverage": {
            "zip": round(sum(1 for r in last12 if r["zip"]) / n12 * 100, 1),
            "area": round(sum(1 for r in last12 if r["key"]) / n12 * 100, 1),
            "coordinates": round(sum(1 for r in last12 if r["lat"] is not None) / n12 * 100, 1),
            "sqft": round(sum(1 for r in last12 if r["sqft"]) / n12 * 100, 1),
        },
    }


def _dump(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, separators=(",", ":"))


def build_index(out_dir, facts, runs, generated_at):
    """index.json drives the city switcher and home page; status.json drives the pipeline page."""
    cities = []
    for city, cfg in CITIES.items():
        f = facts.get(city)
        if not f:
            continue
        cities.append({
            "id": city, "name": cfg["name"], "state": cfg["state"], "center": cfg["center"], "zoom": cfg["zoom"],
            "areaNoun": cfg["area_noun"], "notes": cfg["notes"],
            "sources": cfg["sources"], "crimeSource": CRIME[city]["publisher"], "crimeLink": CRIME[city]["link"],
            **{k: f[k] for k in ("rows", "firstSale", "latestSale", "through", "areas", "kpi", "spark", "coverage", "context", "byMonth")},
        })
    _dump(os.path.join(out_dir, "index.json"), {"generatedAt": generated_at, "cities": cities})
    history = [{
        "id": r["run_id"], "started": r["started"], "duration": r["duration_s"], "mode": r["mode"], "ok": r["ok"],
        "requests": r["http"]["requests"], "retries": r["http"]["retries"],
        "new": sum(s["new"] for c in r["cities"].values() for s in c["sources"]),
        "updated": sum(s["updated"] for c in r["cities"].values() for s in c["sources"]),
        "fetched": sum(s["fetched"] for c in r["cities"].values() for s in c["sources"]),
        "warnings": sum(1 for c in r["checks"] if c["status"] == "warn"),
        "failures": sum(1 for c in r["checks"] if c["status"] == "fail"),
    } for r in runs[-60:]]
    _dump(os.path.join(out_dir, "status.json"),
          {"generatedAt": generated_at, "last": runs[-1] if runs else None, "history": history})
