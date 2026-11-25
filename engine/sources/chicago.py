"""Chicago: Cook County Assessor parcel sales for the City of Chicago townships.

The sales file carries only a parcel number, so three more assessor datasets
are joined in by parcel: location (coordinates, ZIP, community area), street
address, and building characteristics. Those lookups are cached in the store
and only fetched for parcels the engine has not seen before.
"""
import re

from ..http import socrata_pages, socrata_updated_at, sql_text
from ..schema import CONDO, MULTI, SINGLE, title, to_int
from . import SourceRun

BASE = "https://datacatalog.cookcountyil.gov"
SALES = "wvhk-k5uv"
UNIVERSE = "pabr-t5kh"
ADDRESSES = "3723-97qp"
CHARACTERISTICS = "x54s-btds"
CITY_TOWNSHIPS = "('70','71','72','73','74','75','76','77')"
SINGLE_CLASSES = {"202", "203", "204", "205", "206", "207", "208", "209", "210", "234", "278", "295"}
LOOKUP_COLUMNS = ["lat", "lon", "zip", "area", "address", "sqft", "yb", "beds"]
BULK_THRESHOLD = 15000   # more missing parcels than this: page the whole dataset instead
BATCH = 250
PARKING_UNIT = re.compile(r"\s(P|PU|PS|GU|G)-?\d+\s*$", re.I)   # condo "units" that are parking spaces


def classify(cls):
    if cls in SINGLE_CLASSES:
        return SINGLE
    if cls == "211":
        return MULTI
    if cls == "299":
        return CONDO
    return None


def _fetch_by_pin(ctx, dataset, select, pins, bulk_where, year_clause=""):
    """Rows for the given parcels: batched `in` queries, or a full paged pull when there are many."""
    pins = sorted(pins)
    if len(pins) > BULK_THRESHOLD:
        wanted = set(pins)
        for row in socrata_pages(ctx.client, BASE, dataset, select, bulk_where):
            if row.get("pin") in wanted:
                yield row
        return
    for i in range(0, len(pins), BATCH):
        chunk = ",".join(sql_text(p) for p in pins[i:i + BATCH])
        where = f"pin in ({chunk})" + (f" and {year_clause}" if year_clause else "")
        yield from socrata_pages(ctx.client, BASE, dataset, select, where)


def enrich(ctx, pins):
    """Make sure every parcel in `pins` has a cached lookup row; return the lookup."""
    lookup = ctx.store.read_lookup("chicago_parcels")
    missing = {p for p in pins if p not in lookup}
    if not missing:
        return lookup
    ctx.log(f"  looking up {len(missing):,} new parcels")
    found = {p: {} for p in missing}
    for row in _fetch_by_pin(ctx, UNIVERSE, "pin, lat, lon, zip_code, chicago_community_area_name",
                             missing, "triad_code = '1'"):
        found[row["pin"]].update(lat=row.get("lat", ""), lon=row.get("lon", ""), zip=row.get("zip_code", ""),
                                 area=row.get("chicago_community_area_name", ""))
    year = to_int(ctx.client.get_json(f"{BASE}/resource/{ADDRESSES}.json", {"$query": "select max(year)"})[0]["max_year"])
    for row in _fetch_by_pin(ctx, ADDRESSES, "pin, prop_address_full, prop_address_zipcode_1", missing,
                             f"year = {year} and prop_address_city_name = 'CHICAGO'", f"year = {year}"):
        rec = found[row["pin"]]
        rec["address"] = row.get("prop_address_full", "")
        rec.setdefault("zip", row.get("prop_address_zipcode_1", ""))
        if not rec.get("zip"):
            rec["zip"] = row.get("prop_address_zipcode_1", "")
    year = to_int(ctx.client.get_json(f"{BASE}/resource/{CHARACTERISTICS}.json", {"$query": "select max(year)"})[0]["max_year"])
    for row in _fetch_by_pin(ctx, CHARACTERISTICS, "pin, char_bldg_sf, char_yrblt, char_beds", missing,
                             f"year = {year} and township_code in {CITY_TOWNSHIPS}", f"year = {year}"):
        rec = found[row["pin"]]
        rec["sqft"] = (to_int(rec.get("sqft")) or 0) + (to_int(row.get("char_bldg_sf")) or 0)   # multi-building parcels
        rec["beds"] = (to_int(rec.get("beds")) or 0) + (to_int(row.get("char_beds")) or 0)
        rec.setdefault("yb", to_int(row.get("char_yrblt")) or "")
    lookup.update(found)
    ctx.store.write_lookup("chicago_parcels", lookup, LOOKUP_COLUMNS)
    return lookup


def normalize(norm, row, lookup):
    info = lookup.get(row.get("pin") or "", {})
    norm.peek(date=row.get("sale_date"), price=row.get("sale_price"),
              address=title(info.get("address")) or f"Parcel {row.get('pin') or '?'}", kind=f"Class {row.get('class') or '?'}")
    if row.get("is_multisale"):
        return norm.reject("deed covers several properties")
    if row.get("sale_filter_deed_type"):
        return norm.reject("non-market deed type (quit claim etc.)")
    if row.get("sale_filter_same_sale_within_365"):
        return norm.reject("duplicate of a sale within a year")
    pin = row.get("pin") or ""
    info = lookup.get(pin, {})
    kind = classify(str(row.get("class") or ""))
    if kind == CONDO and PARKING_UNIT.search(info.get("address") or ""):
        return norm.reject("parking space or garage unit, not a home")
    return norm.sale(
        id=row.get("row_id") or f"{row.get('doc_no')}-{pin}", date=row.get("sale_date"),
        price=row.get("sale_price"), type=kind,
        address=title(info.get("address")), zip=info.get("zip"), area=title(info.get("area")),
        sqft=info.get("sqft"), year_built=info.get("yb"), beds=info.get("beds"),
        lat=info.get("lat"), lon=info.get("lon"), parcel=pin,
    )


def harvest(ctx):
    run = SourceRun(ctx, "chicago", "Assessor parcel sales + parcel lookups", SALES)
    stamp = socrata_updated_at(ctx.client, BASE, SALES)
    if not ctx.full and stamp and stamp == ctx.state.get("sales_updated_at"):
        return run.skip("source unchanged since last pull")
    select = ("pin, class, sale_date, sale_price, doc_no, row_id, is_multisale, "
              "sale_filter_deed_type, sale_filter_same_sale_within_365")
    rows = list(socrata_pages(ctx.client, BASE, SALES, select,
                              f"sale_date >= '{ctx.since}' and township_code in {CITY_TOWNSHIPS}", order="row_id"))
    keep = [r for r in rows if classify(str(r.get("class") or "")) and (to_int(r.get("sale_price")) or 0) >= 10000]
    lookup = enrich(ctx, {r["pin"] for r in keep if r.get("pin")})
    for row in rows:
        run.add(normalize(run.norm, row, lookup))
    run.finish()
    ctx.state["sales_updated_at"] = stamp
