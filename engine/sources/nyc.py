"""New York City: Department of Finance property sales, with recent deeds from ACRIS.

Two Finance datasets on NYC Open Data: the annualized file (complete past years, with
coordinates) and the rolling file (the latest 12 months). Rolling rows are only
used for dates after the annualized file ends, so the two never double count.

The rolling file runs about six weeks behind the city's deed register (ACRIS). Deeds dated
after the newest Finance sale fill that tail. Each carries an `acris-<document id>` id; once
Finance's files reach a deed's date, Finance is the record for that day and the deed is
dropped (from the store at the next harvest, and from the analytics straight away).
"""
import hashlib
import re
from collections import Counter, defaultdict

from ..http import socrata_pages, socrata_updated_at, sql_date
from ..schema import CONDO, COOP, MIN_PRICE, MULTI, SINGLE, clean_text, title
from . import SourceRun

BASE = "https://data.cityofnewyork.us"
ANNUAL = "w2pb-icbu"
ROLLING = "usep-8jbt"
MASTER = "bnx9-e6tj"     # ACRIS real property master: one row per recorded document
LEGALS = "8h5j-fqxa"     # ACRIS real property legals: the lots each document covers
PLUTO = "64uk-42ks"      # Primary Land Use Tax Lot Output: building class, ZIP and location per tax lot
BOROUGHS = {"1": "Manhattan", "2": "Bronx", "3": "Brooklyn", "4": "Queens", "5": "Staten Island"}
CATEGORY = {
    "01": SINGLE, "02": MULTI, "03": MULTI,
    "04": CONDO, "12": CONDO, "13": CONDO, "15": CONDO,
    "09": COOP, "10": COOP, "17": COOP,
}
FIELDS = ("borough, neighborhood, building_class_category, block, lot, address, zip_code, "
          "gross_square_feet, year_built, sale_price, sale_date")

ACRIS_PREFIX = "acris-"
# Plain deeds only: 'DEED, TS' (timeshare), 'DEED, LE' (life estate) and 'DEEDO' (other) are not home sales
DEED_TYPES = ("DEED",)
# ACRIS property types for one- to three-family houses and residential condo units. Co-op apartments are sold as
# shares, not by deed, so the deed register cannot cover them.
HOME_PROPERTY_TYPES = {"D1", "D2", "D3", "RG", "RV", "RP", "F1", "SC"}
LEGAL_FIELDS = ("document_id, borough, block, lot, property_type, partial_lot, easement, air_rights, "
                "subterranean_rights, street_number, street_name, unit")
PLUTO_FIELDS = "bbl, borocode, block, lot, bldgclass, address, zipcode, latitude, longitude, bldgarea, yearbuilt"
BATCH = 150

R_MULTI_LOT = "deed covers several lots"
R_NOT_HOME = "not a house or residential condo unit"
R_PART = "part of a lot, or air or development rights"
R_COOP_BUILDING = "deed for a whole co-op building"
R_UNCLASSIFIED = "lot not found in earlier sales or PLUTO (cannot classify)"
R_NO_LEGALS = "no lot recorded for the deed"


def class_type(cls):
    """Home type for a building class, as Finance groups classes into its building class categories."""
    cls = clean_text(cls).upper()
    if cls[:1] == "A" or cls in ("S0", "S1"):
        return SINGLE
    if cls[:1] == "B" or cls in ("S2", "C0"):
        return MULTI
    if cls in ("R1", "R2", "R3", "R4", "R6", "R7"):
        return CONDO
    if cls in ("C6", "C8", "D0", "D4", "R9"):
        return COOP
    return None


def normalize(norm, row):
    cat = clean_text(row.get("building_class_category"))[:2]
    kind = CATEGORY.get(cat)
    boro = str(row.get("borough") or "").strip()
    date = str(row.get("sale_date") or "")[:10]
    price = row.get("sale_price")
    address = clean_text(row.get("address"))
    parcel = f"{boro}-{row.get('block')}-{row.get('lot')}"
    key = hashlib.sha1(f"{parcel}|{date}|{price}|{address}".encode()).hexdigest()[:16]
    norm.peek(date=date, price=price, address=title(address), kind=title(clean_text(row.get("building_class_category"))[3:]))
    return norm.sale(
        id=key, date=date, price=price, type=kind, address=title(address),
        zip=row.get("zip_code"), area=title(row.get("neighborhood")), group=BOROUGHS.get(boro, ""),
        sqft=row.get("gross_square_feet") if kind in (SINGLE, MULTI) else None,
        year_built=row.get("year_built"), lat=row.get("latitude"), lon=row.get("longitude"),
        parcel=parcel,
    )


def harvest(ctx):
    # --- annualized: complete calendar years ---
    run = SourceRun(ctx, "nyc", "Annualized sales (complete years)", ANNUAL)
    stamp = socrata_updated_at(ctx.client, BASE, ANNUAL)
    if not ctx.full and stamp and stamp == ctx.state.get("annual_updated_at") and ctx.state.get("annual_max_date"):
        run.skip("source unchanged since last pull")
    else:
        start = ctx.since if ctx.full else ctx.state.get("backfill_start", ctx.since)
        max_date = ""
        for row in socrata_pages(ctx.client, BASE, ANNUAL, FIELDS + ", latitude, longitude",
                                 f"sale_date >= '{sql_date(start)}'"):
            max_date = max(max_date, str(row.get("sale_date") or "")[:10])
            run.add(normalize(run.norm, row))
        run.finish()
        ctx.state.update(annual_updated_at=stamp, annual_max_date=max_date, backfill_start=start)

    # --- rolling: the latest 12 months ---
    run = SourceRun(ctx, "nyc", "Rolling sales (latest 12 months)", ROLLING)
    stamp = socrata_updated_at(ctx.client, BASE, ROLLING)
    if not ctx.full and stamp and stamp == ctx.state.get("rolling_updated_at") and ctx.state.get("finance_max_date"):
        run.skip("source unchanged since last pull")
    else:
        cutoff = ctx.state.get("annual_max_date", "")
        max_date = cutoff
        for row in socrata_pages(ctx.client, BASE, ROLLING, FIELDS, f"sale_date > '{sql_date(cutoff)}'" if cutoff else ""):
            max_date = max(max_date, str(row.get("sale_date") or "")[:10])
            run.add(normalize(run.norm, row))
        run.finish()
        ctx.state.update(rolling_updated_at=stamp, finance_max_date=max_date)

    harvest_deeds(ctx, max(ctx.state.get("finance_max_date") or "", ctx.state.get("annual_max_date") or ""))


# ---- recent deeds (ACRIS) ----------------------------------------------------

def _parcel(legal):
    """borough-block-lot, written the way Finance's rows write it ('' if the record is malformed)."""
    try:
        return f"{str(legal['borough']).strip()}-{int(legal['block'])}-{int(legal['lot'])}"
    except (KeyError, TypeError, ValueError):
        return ""


def _street(value):
    """Street name in a comparable form: '11TH STREET' and '11 STREET' are the same street."""
    return re.sub(r"\b(\d+)(ST|ND|RD|TH)\b", r"\1", clean_text(value).upper())


def _house_number(value):
    digits = re.sub(r"\D", "", str(value or "").split("-")[-1])
    return int(digits) if digits else None


def _condo_building(candidates, legal):
    """The condo building on the block that a unit deed belongs to: same street, nearest house number."""
    if not candidates:
        return None
    street, number = _street(legal.get("street_name")), _house_number(legal.get("street_number"))
    same = [c for c in candidates if _street(" ".join(c.get("address", "").split(" ")[1:])) == street]
    if same and number is not None:
        return min(same, key=lambda c: abs((_house_number(c.get("address", "").split(" ")[0]) or 10 ** 6) - number))
    return (same or candidates)[0]


def resolve_lot(legal, known, pluto_lots, condo_lots, blocks):
    """What an earlier Finance sale or PLUTO says about the lot a deed covers. Returns (fields, reject reason)."""
    parcel = _parcel(legal)
    boro, block = parcel.rsplit("-", 1)[0].split("-")
    named = blocks.get(f"{boro}-{block}")      # a new lot takes the neighbourhood Finance gives its block
    area, group = named.most_common(1)[0][0] if named else ("", "")
    group = group or BOROUGHS.get(boro, "")
    prior = known.get(parcel)
    if prior:                                   # the lot sold before: Finance already classified it
        if prior["type"] == COOP:
            return None, R_COOP_BUILDING
        return {"type": prior["type"], "address": prior["address"], "zip": prior["zip"], "area": prior["area"] or area,
                "group": prior["group"] or group, "sqft": prior["sqft"] or None, "year_built": prior["year_built"] or None,
                "lat": prior["lat"] or None, "lon": prior["lon"] or None, "parcel": parcel}, None
    unit = clean_text(legal.get("unit"))
    street = f"{clean_text(legal.get('street_number'))} {clean_text(legal.get('street_name'))}".strip()
    if legal.get("property_type") == "SC":     # condo unit lots are not in PLUTO; the building's billing lot is
        building = _condo_building(condo_lots.get(f"{boro}-{block}", []), legal)
        if not building:
            return None, R_UNCLASSIFIED
        kind = class_type(building.get("bldgclass")) or CONDO
        if kind == COOP:
            return None, R_COOP_BUILDING
        address = title(street) + (f", {unit.upper()}" if unit else "")
        return {"type": CONDO, "address": address, "zip": building.get("zipcode"), "area": area, "group": group,
                "sqft": None, "year_built": building.get("yearbuilt"), "lat": building.get("latitude"),
                "lon": building.get("longitude"), "parcel": parcel}, None
    p = pluto_lots.get(parcel)
    if not p:
        return None, R_UNCLASSIFIED
    kind = class_type(p.get("bldgclass"))
    if kind == COOP:
        return None, R_COOP_BUILDING
    if kind not in (SINGLE, MULTI):
        return None, R_NOT_HOME
    return {"type": kind, "address": title(p.get("address") or street), "zip": p.get("zipcode"), "area": area,
            "group": group, "sqft": p.get("bldgarea"), "year_built": p.get("yearbuilt"), "lat": p.get("latitude"),
            "lon": p.get("longitude"), "parcel": parcel}, None


def normalize_deed(norm, master, legals, known, pluto_lots, condo_lots, blocks):
    """One ACRIS deed as a sale, or a counted rejection."""
    first = legals[0] if legals else {}
    street = clean_text(f"{first.get('street_number') or ''} {first.get('street_name') or ''}")
    norm.peek(date=master.get("document_date"), price=master.get("document_amt"),
              address=title(street) + (f" #{first['unit']}" if first.get("unit") else ""), kind=f"Deed · {first.get('property_type') or '?'}")
    lots = {_parcel(l) for l in legals}
    if not lots or "" in lots:
        return norm.reject(R_NO_LEGALS)
    if len(lots) > 1:
        return norm.reject(R_MULTI_LOT)
    legal = legals[0]
    if legal.get("property_type") not in HOME_PROPERTY_TYPES:
        return norm.reject(R_NOT_HOME)
    if legal.get("partial_lot") == "P" or "Y" in (legal.get("air_rights"), legal.get("subterranean_rights"), legal.get("easement")):
        return norm.reject(R_PART)
    fields, why = resolve_lot(legal, known, pluto_lots, condo_lots, blocks)
    if why:
        return norm.reject(why)
    return norm.sale(id=ACRIS_PREFIX + master["document_id"], date=master.get("document_date"),
                     price=master.get("document_amt"), **fields)


def _in(values):
    return ",".join("'" + str(v).replace("'", "''") + "'" for v in values)


def stored_lots(store):
    """Finance's latest sale of each lot, and the neighbourhoods each block's sales name."""
    known, blocks = {}, defaultdict(Counter)
    for r in store.iter_rows("nyc"):
        if r["id"].startswith(ACRIS_PREFIX) or not r["parcel"]:
            continue
        if r["parcel"] not in known or r["date"] > known[r["parcel"]]["date"]:
            known[r["parcel"]] = r
        if r["area"]:
            blocks[r["parcel"].rsplit("-", 1)[0]][(r["area"], r["group"])] += 1
    return known, blocks


def harvest_deeds(ctx, reach):
    """Deeds dated after the newest Finance sale, joined to their lot and classified like Finance's rows."""
    run = SourceRun(ctx, "nyc", "Recent deeds (ACRIS)", f"{MASTER} ⨝ {LEGALS}")
    if not reach:
        return run.skip("no Finance sales to follow yet")
    stamp = socrata_updated_at(ctx.client, BASE, MASTER)
    if not ctx.full and stamp and stamp == ctx.state.get("acris_updated_at") and reach == ctx.state.get("acris_reach"):
        return run.skip("source unchanged since last pull")
    types = _in(DEED_TYPES)
    masters = list(socrata_pages(ctx.client, BASE, MASTER, "document_id, doc_type, document_date, document_amt",
                                 f"doc_type in ({types}) and document_date > '{sql_date(reach)}T23:59:59' and document_amt >= {MIN_PRICE}"))
    legals = defaultdict(list)
    ids = sorted({m["document_id"] for m in masters})
    for i in range(0, len(ids), BATCH):
        for row in socrata_pages(ctx.client, BASE, LEGALS, LEGAL_FIELDS, f"document_id in ({_in(ids[i:i + BATCH])})"):
            legals[row["document_id"]].append(row)

    known, blocks = stored_lots(ctx.store)
    # PLUTO: building class, ZIP and location for single-lot home deeds on lots Finance has not sold since the backfill
    need_lots, need_blocks = set(), set()
    for m in masters:
        L = legals.get(m["document_id"], [])
        if len({_parcel(l) for l in L}) != 1 or L[0].get("property_type") not in HOME_PROPERTY_TYPES:
            continue
        l = L[0]
        if not _parcel(l) or _parcel(l) in known:
            continue
        if str(l["borough"]).strip() not in BOROUGHS:   # pasted into the PLUTO queries below
            continue
        if l.get("property_type") == "SC":
            need_blocks.add((str(l["borough"]).strip(), int(l["block"])))
        else:
            need_lots.add(f"{str(l['borough']).strip()}{int(l['block']):05d}{int(l['lot']):04d}")
    pluto_lots, condo_lots = {}, defaultdict(list)
    need_lots = sorted(need_lots)
    for i in range(0, len(need_lots), BATCH):
        for p in socrata_pages(ctx.client, BASE, PLUTO, PLUTO_FIELDS, f"bbl in ({','.join(need_lots[i:i + BATCH])})"):
            pluto_lots[f"{p['borocode']}-{int(p['block'])}-{int(p['lot'])}"] = p
    need_blocks = sorted(need_blocks)
    for i in range(0, len(need_blocks), 60):
        where = " or ".join(f"(borocode='{b}' and block='{k}')" for b, k in need_blocks[i:i + 60])
        for p in socrata_pages(ctx.client, BASE, PLUTO, PLUTO_FIELDS, f"bldgclass like 'R%' and ({where})"):
            if int(p["lot"]) >= 7501:            # the condo's billing lot
                condo_lots[f"{p['borocode']}-{int(p['block'])}"].append(p)

    sales = []
    for m in masters:
        sale = normalize_deed(run.norm, m, legals.get(m["document_id"], []), known, pluto_lots, condo_lots, blocks)
        if sale:
            sales.append(sale)
    # hand-over: deeds Finance now covers, and deeds no longer in the register, leave the store
    keep = {s["id"] for s in sales}
    run.report["removed"] = ctx.store.remove(
        "nyc", lambda r: r["id"].startswith(ACRIS_PREFIX) and (r["date"] <= reach or r["id"] not in keep),
        since=min(reach, ctx.state.get("acris_reach") or reach)[:7])
    for s in sales:
        run.add(s)
    run.finish()
    ctx.state.update(acris_updated_at=stamp, acris_reach=reach)


# ---- analytics: one record per sale --------------------------------------------

def prepare_rows(rows, log=print):
    """Rows the analytics should use. Two NYC-specific rules:

    - an ACRIS deed dated on or before the newest Finance sale is left out: Finance is the record for those days, and
      its own row for the same sale has a different id;
    - Finance lists a deed that covers several lots once per lot, each with the deed's full price. Several lots on one
      block sold on the same day for exactly the same price are one such deed, so none of them is a per-home price.
    """
    reach = max((r["date"] for r in rows if not r["id"].startswith(ACRIS_PREFIX)), default="")
    groups = defaultdict(set)
    for r in rows:
        if r["parcel"] and not r["id"].startswith(ACRIS_PREFIX):
            groups[(r["parcel"].rsplit("-", 1)[0], r["date"], r["price"])].add(r["parcel"])
    out, handed, multi = [], 0, 0
    for r in rows:
        if r["id"].startswith(ACRIS_PREFIX):
            if r["date"] <= reach:
                handed += 1
                continue
        elif r["parcel"] and len(groups[(r["parcel"].rsplit("-", 1)[0], r["date"], r["price"])]) > 1:
            multi += 1
            continue
        out.append(r)
    if handed or multi:
        log(f"  left out {multi:,} Finance rows of deeds covering several lots, {handed:,} ACRIS deeds Finance now covers")
    return out
