"""Philadelphia: recorded deed transfers joined to the city's property records.

Source: City of Philadelphia open data (Department of Records real estate
transfers + Office of Property Assessment properties), via the public Carto
SQL API. The join runs on the city's server, so each sale arrives with the
home's size, type and assessed value attached.
"""
import datetime as dt
from collections import Counter

from ..http import sql_int
from ..schema import CONDO, MULTI, SINGLE, title
from . import SourceRun

API = "https://phl.carto.com/api/v2/sql"
PAGE = 20000

SQL = """
select r.cartodb_id cid, r.document_id doc, r.recording_date rec, r.display_date, r.street_address, r.unit_num,
       left(r.zip_code, 5) zip, r.total_consideration price, r.property_count pc,
       r.opa_account_num opa, ST_Y(r.the_geom) lat, ST_X(r.the_geom) lon,
       o.category_code_description cat, o.building_code_description bdesc,
       o.building_code_description_new bdesc2, o.total_livable_area sqft,
       o.year_built yb, o.number_of_bedrooms beds, o.market_value mv
from rtt_summary r
left join opa_properties_public o on o.parcel_number = r.opa_account_num
where r.document_type = 'DEED' and r.display_date >= '{since}' and r.cartodb_id > {last}
order by r.cartodb_id limit {page}
"""


def classify(row):
    cat = (row.get("cat") or "").upper()
    desc = f"{row.get('bdesc') or ''} {row.get('bdesc2') or ''}".upper()
    if cat == "SINGLE FAMILY":
        return CONDO if "CONDO" in desc else SINGLE
    if cat == "MULTI FAMILY":
        return MULTI
    return None


# Condo deeds rarely carry a parcel number, so the city's files cannot give a consistent count of condo sales
# over time. Leaving them out is better than publishing a trend built on a patchy sample.
CONDO_REASON = "condo (the city's records do not identify condo sales consistently)"
TAIL_DAYS = 45     # deeds are recorded up to several weeks after the sale


def sale_id(document):
    """Both city files carry the recorded deed's document number, so it identifies a sale in either."""
    return f"doc-{document}" if document else ""


def normalize(norm, row):
    unit = f" #{row['unit_num']}" if row.get("unit_num") else ""
    norm.peek(date=row.get("display_date"), price=row.get("price"), address=title(row.get("street_address")) + unit,
              kind=title(row.get("cat")) or "No property record")
    if not row.get("cat"):
        return norm.reject("no matching property record")
    if (row.get("pc") or 1) > 1:
        return norm.reject("deed covers several properties")
    kind = classify(row)
    if kind == CONDO:
        return norm.reject(CONDO_REASON)
    address = title(row.get("street_address"))
    if row.get("unit_num"):
        address += f" #{row['unit_num']}"
    return norm.sale(
        id=sale_id(row.get("doc")), date=row.get("display_date"), price=row.get("price"),
        type=kind, address=address, zip=row.get("zip"),
        sqft=row.get("sqft"), year_built=row.get("yb"), beds=row.get("beds"),
        lat=row.get("lat"), lon=row.get("lon"), assessed=row.get("mv"),
        parcel=row.get("opa") or "",
    )


def harvest(ctx):
    run = SourceRun(ctx, "philadelphia", "Deed transfers + property records", "rtt_summary ⨝ opa_properties_public")
    last, multi, docs, sold, newest = 0, set(), set(), {}, ""
    while True:
        sql = " ".join(SQL.format(since=ctx.since, last=last, page=PAGE).split())
        rows = ctx.client.get_json(API, {"q": sql}).get("rows", [])
        for row in rows:
            newest = max(newest, str(row.get("rec") or "")[:10])
            if (row.get("pc") or 1) > 1:
                multi.add(str(row.get("doc")))
            sale = normalize(run.norm, row)
            if sale:
                docs.add(str(row.get("doc")))
                sold.setdefault(sale["parcel"], []).append(sale["date"])
            run.add(sale)
        if len(rows) < PAGE:
            break
        last = sql_int(rows[-1]["cid"])
    run.finish()
    reach = (dt.date.fromisoformat(newest) - dt.timedelta(days=TAIL_DAYS)).isoformat() if newest else ctx.since
    harvest_property_records(ctx, multi, docs, sold, reach)


RECORD_SQL = """
select cartodb_id cid, parcel_number, book_and_page doc, sale_date, sale_price, location, unit, zip_code,
       category_code_description cat, building_code_description bdesc, building_code_description_new bdesc2,
       total_livable_area sqft, year_built yb, number_of_bedrooms beds, market_value mv,
       ST_Y(the_geom) lat, ST_X(the_geom) lon
from opa_properties_public
where category_code_description in ('SINGLE FAMILY', 'MULTI FAMILY')
  and sale_date >= '{since}' and sale_price >= 10000 and cartodb_id > {last}
order by cartodb_id limit {page}
"""


FORCED_SQL = "select distinct document_id doc from rtt_summary where document_type ilike '%sheriff%' and display_date >= '{since}'"


def normalize_record(norm, row, shared=()):
    norm.peek(date=row.get("sale_date"), price=row.get("sale_price"), address=title(row.get("location")), kind=title(row.get("cat")))
    if not row.get("doc"):
        return norm.reject("no deed reference")
    if row["doc"] in shared:
        return norm.reject("deed covers several properties")
    if classify(row) == CONDO:
        return norm.reject(CONDO_REASON)
    date = str(row.get("sale_date") or "")[:10]
    address = title(row.get("location"))
    if row.get("unit") and "#" not in address:
        address += f" #{row['unit']}"
    return norm.sale(
        id=sale_id(row.get("doc")), date=date, price=row.get("sale_price"),
        type=classify(row), address=address, zip=row.get("zip_code"), sqft=row.get("sqft"), year_built=row.get("yb"),
        beds=row.get("beds"), lat=row.get("lat"), lon=row.get("lon"), assessed=row.get("mv"),
        parcel=row.get("parcel_number") or "",
    )


def _days_apart(a, b):
    try:
        return abs((dt.date.fromisoformat(a) - dt.date.fromisoformat(b)).days)
    except ValueError:
        return 10 ** 6


def harvest_property_records(ctx, multi=frozenset(), docs=frozenset(), sold=None, reach=""):
    """The newest sales, from each home's property record.

    The property file runs weeks ahead of the deeds file, so it fills the gap after the newest recorded deeds.
    When the deeds file catches up, the same sale arrives with the same document number and replaces it.
    """
    run = SourceRun(ctx, "philadelphia", "Newest sales from property records", "opa_properties_public")
    last, pulled = 0, []
    while True:
        sql = " ".join(RECORD_SQL.format(since=ctx.since, last=last, page=PAGE).split())
        rows = ctx.client.get_json(API, {"q": sql}).get("rows", [])
        pulled += rows
        if len(rows) < PAGE:
            break
        last = sql_int(rows[-1]["cid"])
    # forced sales are not market prices, and they would skew recent months (a foreclosed home is soon resold)
    forced = {str(r["doc"]) for r in ctx.client.get_json(API, {"q": FORCED_SQL.format(since=ctx.since)}).get("rows", [])}
    seen = Counter(r.get("doc") for r in pulled)
    shared = {doc for doc, n in seen.items() if n > 1}      # one deed, several parcels: the price is not per home
    shared |= multi                                          # ...including deeds the deeds file already flagged
    for row in pulled:
        # the same sale is occasionally filed under a different document number in the two files
        day = str(row.get("sale_date") or "")[:10]
        run.norm.peek(date=day, price=row.get("sale_price"), address=title(row.get("location")), kind=title(row.get("cat")))
        if str(row.get("doc")) not in docs and any(_days_apart(day, d) <= 45 for d in (sold or {}).get(row.get("parcel_number") or "", [])):
            run.norm.reject("already in the deeds file under another document")
            continue
        if day <= reach and str(row.get("doc")) not in docs:
            # for months the deeds file already covers, it alone is the record: a property record keeps only a
            # home's latest sale, so using it for past months would favour recent sales and bend the trend
            run.norm.reject("older than the newest deeds (the deeds file is the record for those months)")
            continue
        if str(row.get("doc")) in forced:
            run.norm.reject("sheriff's deed (forced sale)")
            continue
        run.add(normalize_record(run.norm, row, shared))
    run.finish()
