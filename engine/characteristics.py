"""Property characteristics per parcel, harvested for the price-drivers model (engine/model.py).

Each city that has them registers a harvester (fills a store lookup keyed by parcel) and a feature function (turns one
sale plus its lookup row into the model's inputs). A city with no entry simply gets no model.

Caveat carried into the output: these are the CURRENT property records, not the record as of each sale date. A home
renovated after a 2020 sale shows today's condition against the 2020 price.

Philadelphia: the Office of Property Assessment's `opa_properties_public` table on the city's Carto SQL API. Only
residential parcels (single and multi family) are read, and only parcels that appear in the stored sales are kept, so
the lookup stays small. Owner and mailing fields are never requested.
"""
import datetime as dt
import math

from .http import FetchError
from .schema import MULTI, to_int

PHL_API = "https://phl.carto.com/api/v2/sql"
PHL_PAGE = 20000
PHL_SQL = """
select cartodb_id cid, parcel_number p, interior_condition ic, exterior_condition ec, number_of_bathrooms baths,
       number_of_bedrooms beds, number_stories stories, total_livable_area sqft, total_area lot, garage_spaces garage,
       central_air air, basements bsmt, quality_grade grade, view_type vw, fireplaces fp, year_built yb,
       building_code_description_new bdesc, building_code_description bdesc_old, market_value mv
from opa_properties_public
where category_code_description in ('SINGLE FAMILY', 'MULTI FAMILY') and cartodb_id > {last}
order by cartodb_id limit {page}
"""
PHL_COLUMNS = ["ic", "ec", "baths", "beds", "stories", "sqft", "lot", "garage", "air", "bsmt", "grade", "vw", "fp",
               "yb", "form", "mv"]
REFRESH_DAYS = 7      # the assessor's file changes slowly; pull it at most weekly


def lookup_name(city):
    return f"{city}_characteristics"


def building_form(new_desc, old_desc):
    """Row, twin, detached or apartments, from the assessor's building description (newer coding first)."""
    d = (new_desc or "").upper()
    if d.startswith("ROW") or d.startswith("DOWNTOWN ROW"):
        return "Row"
    if d.startswith("TWIN"):
        return "Twin"
    if d.startswith("APART") or d.startswith("APTS"):
        return "Apartments"
    if d in {"CONVENTIONAL", "CAPE", "COLONIAL", "SPLIT LEVEL", "RANCH", "OLD STYLE", "BUNGALOW", "BI LEVEL",
             "CONTEMPORARY", "TUDOR"}:
        return "Detached"
    if d:
        return "Other"
    o = (old_desc or "").upper()
    if o.startswith("ROW"):
        return "Row"
    if o.startswith("S/D"):
        return "Twin"
    if o.startswith("DET"):
        return "Detached"
    return ""


def harvest_philadelphia(ctx, report, city="philadelphia"):
    name = lookup_name(city)
    held = ctx.store.read_lookup(name)
    pulled = ctx.state.get("characteristics_pulled")
    fresh = pulled and (ctx.today - dt.date.fromisoformat(pulled)).days < REFRESH_DAYS
    if held and fresh and not ctx.full:
        report["skipped"] = f"pulled {pulled}; refreshed at most weekly"
        return
    wanted = {r["parcel"] for r in ctx.store.iter_rows(city) if r["parcel"]}
    out, last = {}, 0
    while True:
        sql = " ".join(PHL_SQL.format(last=last, page=PHL_PAGE).split())
        rows = ctx.client.get_json(PHL_API, {"q": sql}).get("rows")
        if rows is None:
            raise FetchError("opa_properties_public: unexpected response")
        for row in rows:
            report["fetched"] += 1
            if row.get("p") not in wanted:
                report["rejected"]["parcel has no stored sale"] = report["rejected"].get("parcel has no stored sale", 0) + 1
                continue
            rec = {c: "" if row.get(c) is None else str(row[c]).strip() for c in PHL_COLUMNS if c != "form"}
            rec["form"] = building_form(row.get("bdesc"), row.get("bdesc_old"))
            out[row["p"]] = rec
            report["kept"] += 1
        if len(rows) < PHL_PAGE:
            break
        last = rows[-1]["cid"]
    if not out:
        raise FetchError("no characteristics matched the stored sales")
    report["new"] = sum(1 for k in out if k not in held)
    report["updated"] = sum(1 for k, v in out.items() if k in held and any(held[k].get(c, "") != v[c] for c in PHL_COLUMNS))
    report["unchanged"] = len(out) - report["new"] - report["updated"]
    ctx.store.write_lookup(name, out, PHL_COLUMNS)
    ctx.state["characteristics_pulled"] = ctx.today.isoformat()


HISTORY_SQL = """
select parcel_number p, string_agg(year || ':' || round(market_value), ';' order by year) v
from assessments where year >= '{first}' and parcel_number > '{last}'
group by 1 order by 1 limit {page}
"""
HISTORY_FIRST = "2018"


def history_name(city):
    return f"{city}_assessment_history"


def harvest_assessment_history(ctx, report, city="philadelphia"):
    """Each sold parcel's certified market value per tax year (the `assessments` table), so a sale can be compared
    with the value certified BEFORE it rather than today's. Re-pulled only when a new tax year appears or the stored
    sales name parcels the lookup does not hold."""
    name = history_name(city)
    held = ctx.store.read_lookup(name)
    latest = str(ctx.client.get_json(PHL_API, {"q": "select max(year) y from assessments"})["rows"][0]["y"])
    wanted = {r["parcel"] for r in ctx.store.iter_rows(city) if r["parcel"]}
    missing = len(wanted - set(held))
    if held and latest == ctx.state.get("assessment_year") and missing < 0.01 * len(wanted) and not ctx.full:
        report["skipped"] = f"tax year {latest} already held ({missing:,} newer parcels wait for the next tax year)"
        return
    out, last = {}, "0"
    while True:
        sql = " ".join(HISTORY_SQL.format(first=HISTORY_FIRST, last=last, page=PHL_PAGE).split())
        rows = ctx.client.get_json(PHL_API, {"q": sql}).get("rows")
        if rows is None:
            raise FetchError("assessments: unexpected response")
        for row in rows:
            report["fetched"] += 1
            if row["p"] not in wanted:
                report["rejected"]["parcel has no stored sale"] = report["rejected"].get("parcel has no stored sale", 0) + 1
                continue
            out[row["p"]] = {"mv": row.get("v") or ""}
            report["kept"] += 1
        if len(rows) < PHL_PAGE:
            break
        last = rows[-1]["p"]
    if not out:
        raise FetchError("no assessment history matched the stored sales")
    report["new"] = sum(1 for k in out if k not in held)
    report["updated"] = sum(1 for k, v in out.items() if k in held and held[k].get("mv") != v["mv"])
    report["unchanged"] = len(out) - report["new"] - report["updated"]
    ctx.store.write_lookup(name, out, ["mv"])
    ctx.state["assessment_year"] = latest


def value_for_year(history_row, year):
    """The certified market value for one tax year from a history row ("2024:150000;2025:180000"), or None."""
    for part in (history_row or {}).get("mv", "").split(";"):
        y, _, v = part.partition(":")
        if y == str(year):
            v = to_int(v)
            return v if v and v >= 10000 else None
    return None


# ---- features: one sale + its lookup row -> the model's inputs ------------------------------------------------------

def _band(value, edges, labels):
    for edge, label in zip(edges, labels):
        if value < edge:
            return label
    return labels[-1]


# OPA condition codes. The official dictionary (metadata.phila.gov) could not be read; labels follow OPA's guidance as
# quoted by NBC10 and phillypropertytax.com (1 new construction, 2 rehabbed/updated, 3 above average, 4 average,
# 5 below average, 6 vacant/substantial damage, 7 sealed/structurally compromised).
CONDITION = {"1": "New construction", "2": "Rehabbed", "3": "Above average", "4": "Average",
             "5": "Below average", "6": "Vacant or damaged", "7": "Sealed or compromised"}
BASEMENT = {"A": "Full, finished", "B": "Full, other", "C": "Full, other", "D": "Full, other",
            "E": "Partial", "F": "Partial", "G": "Partial", "H": "Partial"}
VIEW = {"I": "Typical", "A": "City skyline", "B": "Water", "C": "Park", "D": "Commercial", "E": "Industrial",
        "H": "Landmark"}
GRADE = {"A+": "A", "A": "A", "A-": "A", "B+": "B", "B": "B", "B-": "B", "C+": "C+", "C": "C", "C-": "C-",
         "D+": "D or E", "D": "D or E", "D-": "D or E", "E+": "D or E", "E": "D or E", "E-": "D or E"}

# Driver groups, in display order. kind "log": log of a positive number (effect shown per +10%); "cat": levels against
# a reference level; "headline" names the one contrast shown for the driver in the ranked chart.
PHL_SPEC = [
    {"name": "Floor area", "kind": "log", "unit": "+10% floor area"},
    {"name": "Lot size", "kind": "log", "unit": "+10% lot area"},
    {"name": "Interior condition", "kind": "cat", "ref": "Average", "headline": "Below average",
     "order": ["New construction", "Rehabbed", "Above average", "Average", "Below average", "Vacant or damaged",
               "Sealed or compromised"]},
    {"name": "Exterior condition", "kind": "cat", "ref": "Average", "headline": "Below average",
     "order": ["Rehabbed", "Above average", "Average", "Below average", "Vacant or damaged",
               "Sealed or compromised"]},
    {"name": "Quality grade", "kind": "cat", "ref": "C", "headline": "B", "order": ["A", "B", "C+", "C", "C-", "D or E"]},
    {"name": "Age at sale", "kind": "cat", "ref": "75-99 yrs", "headline": "Under 5 yrs",
     "order": ["Under 5 yrs", "5-19 yrs", "20-49 yrs", "50-74 yrs", "75-99 yrs", "100-124 yrs", "125+ yrs"]},
    {"name": "Building form", "kind": "cat", "ref": "Row", "headline": "Detached",
     "order": ["Row", "Twin", "Detached", "Apartments", "Other"]},
    {"name": "Home type", "kind": "cat", "ref": "Single family", "headline": "Multi-family",
     "order": ["Single family", "Multi-family"]},
    {"name": "Bedrooms", "kind": "cat", "ref": "3", "headline": "4", "order": ["1", "2", "3", "4", "5+"]},
    {"name": "Bathrooms", "kind": "cat", "ref": "1", "headline": "2", "order": ["1", "2", "3+"]},
    {"name": "Stories", "kind": "cat", "ref": "2", "headline": "3+", "order": ["1", "2", "3+"]},
    {"name": "Garage", "kind": "cat", "ref": "None", "headline": "1", "order": ["None", "1", "2+"]},
    {"name": "Central air", "kind": "cat", "ref": "No", "headline": "Yes", "order": ["No", "Yes"]},
    {"name": "Basement", "kind": "cat", "ref": "Full, other", "headline": "Full, finished",
     "order": ["Full, finished", "Full, other", "Partial"]},
    {"name": "Fireplace", "kind": "cat", "ref": "No", "headline": "Yes", "order": ["No", "Yes"]},
    {"name": "View", "kind": "cat", "ref": "Typical", "headline": "City skyline",
     "order": ["Typical", "City skyline", "Water", "Park", "Landmark", "Commercial", "Industrial"]},
]


def philadelphia_features(sale, ch):
    """Model inputs for one Philadelphia sale. None means not recorded (the model gives it its own level)."""
    sqft = to_int(ch.get("sqft")) or sale.get("sqft")
    lot = to_int(ch.get("lot"))
    yb = to_int(ch.get("yb")) or sale.get("yb")
    year = int(sale["date"][:4])
    age = year - yb if yb and 1700 <= yb <= year + 1 else None
    beds = to_int(ch.get("beds")) or sale.get("beds")
    baths = to_int(ch.get("baths"))
    stories = to_int(ch.get("stories"))
    garage = to_int(ch.get("garage"))
    fp = to_int(ch.get("fp"))
    return {
        "Floor area": math.log(sqft) if sqft and 200 <= sqft <= 20000 else None,
        "Lot size": math.log(lot) if lot and 200 <= lot <= 500000 else None,
        "Interior condition": CONDITION.get(ch.get("ic", "")),
        # exterior "new construction" is almost always the same homes as interior "new construction"; keeping both
        # would split one effect between two near-identical columns, so the exterior code joins "rehabbed"
        "Exterior condition": CONDITION.get("2" if ch.get("ec") == "1" else ch.get("ec", "")),
        "Quality grade": GRADE.get(ch.get("grade", "")),
        "Age at sale": _band(max(age, 0), [5, 20, 50, 75, 100, 125],
                             ["Under 5 yrs", "5-19 yrs", "20-49 yrs", "50-74 yrs", "75-99 yrs", "100-124 yrs", "125+ yrs"])
        if age is not None else None,
        "Building form": ch.get("form") or None,
        "Home type": "Multi-family" if sale["type"] == MULTI else "Single family",
        "Bedrooms": (str(beds) if beds < 5 else "5+") if beds and 0 < beds <= 20 else None,
        "Bathrooms": (str(baths) if baths < 3 else "3+") if baths and 0 < baths <= 20 else None,
        "Stories": (str(stories) if stories < 3 else "3+") if stories and 0 < stories <= 10 else None,
        "Garage": ("None" if garage == 0 else "1" if garage == 1 else "2+") if garage is not None and garage <= 10 else None,
        "Central air": {"Y": "Yes", "N": "No"}.get(ch.get("air", "")),
        "Basement": BASEMENT.get(ch.get("bsmt", "")),
        "Fireplace": ("Yes" if fp > 0 else "No") if fp is not None else None,
        "View": VIEW.get(ch.get("vw", "")),
    }


def philadelphia_value(ch):
    """The assessor's current market value, the second baseline."""
    mv = to_int(ch.get("mv"))
    return mv if mv and mv >= 10000 else None


# city -> how to harvest its characteristics and turn them into model inputs. NYC and Chicago join by adding an entry.
CITIES = {
    "philadelphia": {
        "source": "Property characteristics", "dataset": "City of Philadelphia, OPA property records (opa_properties_public)",
        "harvest": harvest_philadelphia, "features": philadelphia_features, "spec": PHL_SPEC,
        "jobs": [("Property characteristics", "City of Philadelphia, OPA property records (opa_properties_public)",
                  harvest_philadelphia),
                 ("Assessment history", "City of Philadelphia, OPA certified values by tax year (assessments)",
                  harvest_assessment_history)],
        "history": history_name("philadelphia"), "value_for_year": value_for_year,
        "value": philadelphia_value, "value_name": "Assessor's market value",
        "caveat": "Characteristics are the city's current property record, not the record at the time of each sale.",
    },
}
