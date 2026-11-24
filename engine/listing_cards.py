"""One enriched file per city for the New listings page: <city>/listing-cards.json. Standard library only.

    python3 -m engine.listing_cards                    every city under web/public/data
    python3 -m engine.listing_cards --city philadelphia --store store
    python3 -m engine.listing_cards --listings harvested-listings   listings.json read from another folder

Reads files already on disk and fetches nothing:

- <city>/listings.json (engine/live_listings.py): one row per home, with asking price, list date, publishers and facts;
- <city>/listing-tags.json (engine/listing_analysis.py, optional): description signals, photo findings, defect crops;
- <city>/areas.json, overview.json, sales.json (engine/analytics.py): recorded sales by area, 12 complete months;
- <city>/model-predictor.json (engine/model.py, optional): the price model's coefficients and backtest error;
- the store (optional): the city's area boundaries, and the parcel records the model prices a home from.

Each home is placed in an area with the boundary file (point in polygon) when it has coordinates and the city has
one, otherwise with listing_analysis.AreaIndex (same address among recorded sales, nearest recorded sale, ZIP code).

The headline number compares the asking price with one of, best first:
  "model"   the hedonic model's estimate for the parcel, when the listing's address matches a recorded sale whose
            parcel has a property record (Philadelphia). Range and typical error come from the model's backtest.
  "ppsf"    the area's recorded 12-month median $/sq ft times the listing's floor area (not for condos or co-ops,
            whose sales carry no floor area).
  "median"  the area's recorded 12-month median sale price (ignores size).
Asking prices are only ever compared with recorded sales; they never enter the sold figures.

Plus and minus items here do not depend on the page's area weights (the area tier is worked out in the browser).
"""
import argparse
import hashlib
import json
import math
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile

from .analytics import area_label
from .cities import CITIES as CITY_CFG
from .listing_analysis import AreaIndex, infer_type, normalize_address, text_of

CITY_IDS = ("philadelphia", "nyc", "chicago")
MODEL_ITEM_PCT = 3.0          # a model driver worth at least this much (either way) becomes a plus or minus item
PPSF_ITEM_PCT = 10.0          # asking $/sq ft this far from the area's recorded $/sq ft becomes an item
GROWTH_ITEM_PTS = 2.0         # area price growth this many points from the city's becomes an item
INCIDENT_ITEM_RATIO = 0.2     # incidents 20% above or below the city figure become an item
PARTS_SHOWN = 8               # model contributions kept per card; the rest are summed into "Other features"
NO_PPSF_TYPES = {"Condo", "Co-op"}
PPSF_PLAUSIBLE = (1 / 3, 3.0)  # asking $/sq ft outside this multiple of the area's: the floor area is likely wrong
RESALE_YEARS = 3              # a recorded sale this recent, well under the asking price, suggests work since
RESALE_RATIO = 1.5

SIGNAL_ITEMS = {   # description signal -> (+1 plus, -1 minus, 0 tag only), short text
    "renovated": (1, "Renovated"), "new_construction": (1, "New construction"), "new_roof": (1, "New roof"),
    "new_systems": (1, "New systems"), "updated_kitchen": (1, "Updated kitchen"), "updated_bath": (1, "Updated bath"),
    "tax_abatement": (1, "Tax abatement"), "vacant_delivery": (0, "Delivered vacant"),
    "as_is": (-1, "Sold as-is"), "needs_work": (-1, "Needs work"), "disclosed_damage": (-1, "Damage disclosed"),
    "investor_special": (-1, "Investor special"), "cash_only": (-1, "Cash only"),
    "financing_limited": (-1, "Financing limits"), "no_repairs_or_inspections": (-1, "No seller repairs"),
    "distressed_sale": (-1, "Short sale / REO / auction"),
    "estate_sale": (0, "Estate sale"), "motivated_seller": (0, "Motivated seller"), "price_reduced": (0, "Price reduced"),
    "tenant_occupied": (0, "Tenant-occupied"), "income_property": (0, "Income property"),
    "development_opportunity": (0, "Development"), "coming_soon_or_off_market": (0, "Coming soon"),
}
DEFECTS = {
    "water_stain_ceiling": "Water-stained ceiling", "water_stain_wall": "Water-stained wall", "visible_mould": "Visible mould",
    "peeling_paint": "Peeling paint", "damaged_flooring": "Damaged floor", "damaged_ceiling_or_drywall": "Damaged ceiling or wall",
    "missing_kitchen_or_bath_fixtures": "Missing fixtures", "broken_windows_or_doors": "Broken window or door",
    "exposed_wiring": "Exposed wiring", "pest_signs": "Signs of pests", "fire_or_smoke_damage": "Fire or smoke damage",
    "roof_damage": "Roof damage", "structural_cracks": "Structural crack", "debris_or_cleanout_needed": "Debris",
    "other_damage": "Other damage",
}


def read_json(path):
    try:
        data = json.loads(Path(path).read_text())
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("w", dir=path.parent, delete=False) as tmp:
        json.dump(data, tmp, separators=(",", ":"), allow_nan=False)
    os.chmod(tmp.name, 0o644)   # a temporary file is private by default; the site serves this one
    os.replace(tmp.name, path)


def _num(v):
    return v if type(v) in (int, float) and math.isfinite(v) else None


def _round_to(v, step):
    return int(round(v / step) * step)


def nice(v):
    """Round an estimate to a sensible step, so it never reads as more precise than it is."""
    return _round_to(v, 1000 if v < 100_000 else 5000 if v < 1_000_000 else 25_000)


def _plural(n, word):
    return f"{n} {word}{'' if n == 1 else 's'}"


# ---------------------------------------------------------------------------- the price model for one parcel

class ParcelIndex:
    """Recorded sales by normalised address, so a listing can be matched to its parcel and property record."""

    def __init__(self, store, city):
        from .characteristics import CITIES, lookup_name
        self.cfg = CITIES.get(city)
        self.city = city
        self.by_address = {}
        self.lookup = store.read_lookup(lookup_name(city)) if self.cfg else {}
        if not self.lookup:
            return
        for row in store.iter_rows(city):
            key = normalize_address(row.get("address"), city)
            if not key or not row.get("parcel") or row["parcel"] not in self.lookup:
                continue
            seen = self.by_address.setdefault(key[:3], {})
            if row["parcel"] not in seen or row["date"] > seen[row["parcel"]]["date"]:
                seen[row["parcel"]] = row

    def match(self, address, zip_code=None):
        """(sale row, property record) when the address names exactly one parcel; else (None, None)."""
        key = normalize_address(address, self.city)
        if not key or not self.by_address:
            return None, None
        hits = self.by_address.get(key[:3]) or {}
        zip_code = (zip_code or key[3] or "")[:5]
        if len(hits) > 1 and zip_code:
            hits = {p: r for p, r in hits.items() if (r.get("zip") or "")[:5] == zip_code}
        if len(hits) != 1:
            return None, None
        row = next(iter(hits.values()))
        return row, self.lookup[row["parcel"]]


def _to_int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def model_features(cfg, sale, record, today):
    """The model's inputs for a home sold before, priced today: today's age, the current property record."""
    sale = {"date": today, "type": sale.get("type"), "sqft": _to_int(sale.get("sqft")),
            "yb": _to_int(sale.get("year_built")), "beds": _to_int(sale.get("beds"))}
    return cfg["features"](sale, record)


def _driver_text(name, level, pct, features, predictor):
    """A few words for one model driver: "Bigger than typical", "Small lot", "Finished basement"."""
    up = pct > 0
    if name in ("Floor area", "Lot size"):
        if features.get(name) is None:
            return f"{name} not recorded"
        if name == "Floor area":
            return "Bigger than typical" if up else "Smaller than typical"
        return "Big lot" if up else "Small lot"
    if level is None:
        return f"{name} not recorded"
    special = {
        ("Basement", "Full, finished"): "Finished basement", ("Basement", "Partial"): "Partial basement",
        ("Central air", "Yes"): "Central air", ("Central air", "No"): "No central air",
        ("Fireplace", "Yes"): "Fireplace", ("Home type", "Multi-family"): "Multi-family",
        ("Building form", "Apartments"): "Apartment building",
    }
    if (name, level) in special:
        return special[(name, level)]
    if name == "Garage":
        return "No garage" if level == "None" else "Garage"
    if name in ("Interior condition", "Exterior condition"):
        return f"{name.split()[0]}: {level.lower()}"
    if name == "Quality grade":
        return f"Quality grade {level}"
    if name == "Age at sale":
        return "Built " + level.replace(" yrs", "").replace("Under ", "<").replace("+", "+") + " yrs ago"
    if name == "Building form":
        return f"{level} home"
    if name in ("Bedrooms", "Bathrooms", "Stories"):
        return f"{level} {name.lower()}" if level != "1" else f"1 {name.lower()[:-1]}"
    if name == "View":
        return f"{level} view"
    return f"{name}: {level}"


def band_error(predictor, method, value):
    """The method's typical (median absolute) backtest error in % for the price band of `value`, else overall."""
    for lo, hi, label in predictor.get("bands") or []:
        if lo <= value < hi:
            for b in predictor.get("errorByBand") or []:
                if b.get("label") == label and (b.get(method) or {}).get("mdape") is not None:
                    return b[method]["mdape"], label
    overall = ((predictor.get("error") or {}).get(method) or {}).get("mdape")
    return overall, None


def model_estimate(predictor, features, area_key, area_name):
    """The model's estimate for one home, with each driver's contribution in %. None if the inputs are unusable.

    log price = intercept + market level + neighbourhood + sum of driver terms. The parts are given against a
    reference home (every driver at its reference level, floor and lot area at the city's typical size) in the
    sales-weighted average neighbourhood, so they multiply out to the estimate."""
    terms = []
    for name, p in (predictor.get("log") or {}).items():
        v = features.get(name)
        b = p["beta"] * (v - p["centre"]) if v is not None else (p["missing"] or 0.0)
        terms.append((name, None, b))
    for name, p in (predictor.get("cat") or {}).items():
        level = features.get(name)
        b = p["levels"].get("Not recorded" if level is None else level, 0.0)
        terms.append((name, level, b))
    area = predictor["areas"].get(area_key, predictor.get("pooled") or 0.0) if area_key else None
    if area is None:
        return None
    base = predictor["intercept"] + predictor["level"]["effect"] + predictor["areaMean"]
    total = base + (area - predictor["areaMean"]) + sum(b for _, _, b in terms)
    value = math.exp(total)
    pct = lambda b: round((math.exp(b) - 1) * 100, 1)
    parts = [{"label": _driver_text(name, level, pct(b), features, predictor), "pct": pct(b), "driver": name,
              **({"notRecorded": True} if (level is None and name in (predictor.get("cat") or {})) or
                 (name in (predictor.get("log") or {}) and features.get(name) is None) else {})}
             for name, level, b in terms if abs(pct(b)) >= 0.5]
    parts.sort(key=lambda p: -abs(p["pct"]))
    rest = parts[PARTS_SHOWN:]
    parts = parts[:PARTS_SHOWN]
    if rest:
        parts.append({"label": f"{_plural(len(rest), 'other feature')}", "pct": pct(sum(math.log1p(p['pct'] / 100) for p in rest)),
                      "driver": "other"})
    hood = pct(area - predictor["areaMean"])
    return {"value": value, "base": math.exp(base), "area": {"label": "Neighbourhood", "name": area_name, "pct": hood},
            "parts": parts}


# ---------------------------------------------------------------------------- one card

def _growth(area):
    return _num(area.get("priceGrowth1")) if _num(area.get("priceGrowth1")) is not None else _num(area.get("medianYoY"))


def _incident_index(violent, prop):
    return violent * 3 + prop if violent is not None and prop is not None else None   # as the Find areas page weighs them


def area_items(area, city_fig):
    """Area facts that do not depend on the page's weights: price trend and incidents against the city."""
    plus, minus = [], []
    g, cg = _growth(area), city_fig.get("growth1")
    if g is not None and cg is not None and abs(g - cg) >= GROWTH_ITEM_PTS:
        text = f"Prices here {f'{g:+.0f}%' if round(g) else 'flat'} in a year (city {cg:+.0f}%)"
        (plus if g > cg else minus).append({"text": text, "kind": "area",
                                            "why": "Recorded sales, 12 complete months, against the year before"})
    v, p = _num(area.get("violentRate")), _num(area.get("propertyRate"))
    idx, cidx = _incident_index(v, p), _incident_index(city_fig.get("violentRate"), city_fig.get("propertyRate"))
    if idx is not None and cidx:
        ratio = idx / cidx - 1
        if abs(ratio) >= INCIDENT_ITEM_RATIO:
            item = {"text": f"{'More' if ratio > 0 else 'Fewer'} incidents than the city figure", "kind": "area",
                    "why": f"Police-reported incidents per 1,000 residents a year: violent {v:.1f}, property {p:.1f} "
                           f"(city {city_fig['violentRate']:.1f}, {city_fig['propertyRate']:.1f})"}
            (minus if ratio > 0 else plus).append(item)
    return plus, minus


def signal_items(signals):
    plus, minus, tags = [], [], []
    for name, quote in (signals or {}).items():
        sign, text = SIGNAL_ITEMS.get(name, (0, name.replace("_", " ")))
        year = re.search(r"\((\d{4})\)", quote or "") or re.search(r"\b(19[89]\d|20[0-4]\d)\b", quote or "")
        if name == "new_roof" and year:
            text += f" {year[1]}"
        item = {"text": text, "kind": "description", "why": f"Description says: “{quote}”"}
        (plus if sign > 0 else minus if sign < 0 else tags).append(item)
    return plus, minus, [t["text"] for t in tags]


def finding_label(f):
    """"Missing fixtures (faucet)" when the finding names its specific thing, else the plain defect label."""
    label = DEFECTS.get(f.get("type"), str(f.get("type", "")).replace("_", " ").capitalize())
    return f"{label} ({f['item']})" if f.get("item") else label


def photo_summary(photo):
    """Findings, crops and the minus items from photo analysis (Philadelphia). Never a clean bill: absence of
    findings is reported only as what the photos showed."""
    if not photo:
        return None, []
    minus, by_type = [], {}
    for f in photo.get("findings") or []:
        by_type.setdefault(f.get("type"), []).append(f)
    crops = []
    for kind, found in by_type.items():
        rooms = sum(f.get("rooms") or 1 for f in found)
        label = DEFECTS.get(kind, str(kind).replace("_", " ").capitalize())
        items = list(dict.fromkeys(f["item"] for f in found if f.get("item")))
        named = f"{label} ({', '.join(items[:3])})" if items else label
        minus.append({"text": f"{named} · {_plural(rooms, 'room')}", "kind": "photo",
                      "why": "; ".join(f"{str(f.get('room', '')).replace('_', ' ')}: {f.get('evidence', '')}" for f in found)})
        crops += [{"src": c, "label": finding_label(f)} for f in found for c in f.get("crops") or []]
    editing = photo.get("editing") or {}
    if editing.get("labelled"):
        minus.append({"text": "Photos virtually staged (labelled)", "kind": "photo",
                      "why": f"Labelled by the publisher: “{editing['labelled'][0].get('text', '')}”"})
    missing = (photo.get("coverage") or {}).get("missing") or []
    if missing:
        minus.append({"text": f"Not shown in photos: {', '.join(missing)}", "kind": "photo",
                      "why": "Expected for this home type but not in the photos analysed"})
    out = {"findings": photo.get("findingCount") or 0, "crops": crops, "credit": photo.get("credit"),
           "creditUrl": photo.get("creditUrl"), "condition": photo.get("condition"),
           "coverage": (photo.get("coverage") or {}).get("rating"), "seen": (photo.get("seenClean") or {}).get("text"),
           "possiblyEdited": bool(editing.get("suspected") or editing.get("emptyAndFurnished")) and not editing.get("labelled")}
    return out, minus


def card_for(row, ctx):
    """One home: facts, area, sold comparison, estimate and the weight-free plus and minus items."""
    city, price = ctx["city"], _num(row.get("price"))
    tag = ctx["tags"].get(row.get("url")) or next((ctx["tags"][u] for u in [s.get("url") for s in row.get("sources") or []]
                                                    if u in ctx["tags"]), None)
    sqft = _num(row.get("sqft")) or _num(((tag or {}).get("compare") or {}).get("sqft"))
    home_type = infer_type(row, city) or ((tag or {}).get("compare") or {}).get("type")
    lat, lon = _num(row.get("lat")), _num(row.get("lon"))
    if lat is not None and not (-90 <= lat <= 90 and -180 <= lon <= 180):
        lat = lon = None
    links = []
    for s in row.get("sources") or [{"source": row.get("source"), "url": row.get("url")}]:
        if s.get("url") and s["url"] not in [x["url"] for x in links]:
            links.append({"source": s.get("source") or row.get("source"), "url": s["url"]})
    card = {"id": row.get("key") or row["url"], "url": row["url"], "address": row.get("address"), "date": row.get("date"),
            "price": price, "source": row.get("source"), "links": links,
            "facts": {k: v for k, v in (("beds", _num(row.get("beds"))), ("baths", _num(row.get("baths"))),
                                        ("sqft", sqft), ("type", row.get("type")), ("homeType", home_type)) if v is not None}}
    if lat is not None and lon is not None:
        card.update(lat=round(lat, 6), lon=round(lon, 6))
    for k in ("firstSeen", "lastSeen"):   # when a publisher's read first and last listed the home (live_listings)
        if isinstance(row.get(k), str):
            card[k] = row[k][:10]
    if row.get("inSnapshot") is False:
        card["inSnapshot"] = False

    # area: point in polygon first, then the recorded-sales placement
    area, mapping = None, "none"
    if ctx["locator"] and lat is not None:
        name = ctx["locator"].find(lat, lon)
        area = ctx["areas_by_key"].get(f"{name}|") if name else None
        mapping = "boundary" if area else mapping
    sale, record = ctx["parcels"].match(row.get("address"), row.get("zip")) if ctx["parcels"] else (None, None)
    if area is None:
        found, how, _, _ = ctx["index"].locate(row)
        area, mapping = (found, how) if found else (None, "none")
    if area is None and tag and (tag.get("compare") or {}).get("area"):
        area, mapping = ctx["areas_by_name"].get(tag["compare"]["area"]), tag["compare"].get("mapping", "tags")
    plus, minus, notes = [], [], []
    no_estimate = None
    last = None   # the home's latest recorded sale (never mixed with the asking price, only shown beside it)
    if sale:
        last = {"date": sale["date"], "price": _to_int(sale.get("price"))}
    elif ((tag or {}).get("compare") or {}).get("priorSale", {}).get("price"):
        last = {k: tag["compare"]["priorSale"].get(k) for k in ("date", "price")}
    if last and last.get("price") and last.get("date"):
        card["lastSale"] = last
        if price and price >= RESALE_RATIO * last["price"] and last["date"] >= f"{int(ctx['today'][:4]) - RESALE_YEARS}{ctx['today'][4:]}":
            card["lastSale"]["recentResale"] = True
    if area:
        label = ctx["label"](area["key"])
        card["area"] = {"key": area["key"], "name": label, "mapping": mapping}
        sold = {"ppsf12": _num(area.get("ppsf12")), "median12": _num(area.get("median12")), "sales12": area.get("sales12"),
                "topType": area.get("topType"), "topTypeShare": area.get("topTypeShare")}
        if price and sqft and sold["ppsf12"] and home_type not in NO_PPSF_TYPES:
            ratio = price / sqft / sold["ppsf12"]
            if PPSF_PLAUSIBLE[0] <= ratio <= PPSF_PLAUSIBLE[1]:
                sold["vsPpsfPct"] = round(100 * (ratio - 1), 1)
            else:
                no_estimate = "Floor area on the listing looks wrong"
        if price and sold["median12"]:
            sold["vsMedianPct"] = round(100 * (price / sold["median12"] - 1), 1)
        card["sold"] = {k: v for k, v in sold.items() if v is not None}
        p, m = area_items(area, ctx["cityFigures"])
        plus += p
        minus += m
        vs = sold.get("vsPpsfPct")
        if vs is not None and abs(vs) >= PPSF_ITEM_PCT:
            item = {"text": f"$/sq ft {abs(vs):.0f}% {'under' if vs < 0 else 'over'} sold homes here",
                    "kind": "sold", "why": f"${price / sqft:,.0f} asking vs ${sold['ppsf12']:,.0f} median sold, 12 complete months"}
            (plus if vs < 0 else minus).append(item)

    # the headline estimate
    predictor = ctx["predictor"]
    estimate = None
    if predictor and record is not None and area:
        features = model_features(ctx["parcels"].cfg, sale, record, ctx["today"])
        est = model_estimate(predictor, features, card["area"]["key"], card["area"]["name"])
        if est:
            err, band = band_error(predictor, "model", est["value"])
            estimate = {"kind": "model", "value": nice(est["value"]), "base": nice(est["base"]), "area": est["area"],
                        "parts": est["parts"], "error": err, "errorBand": band}
            for part in est["parts"]:
                if part.get("notRecorded") or part["driver"] == "other" or abs(part["pct"]) < MODEL_ITEM_PCT:
                    continue
                (plus if part["pct"] > 0 else minus).append({"text": part["label"], "kind": "model",
                                                              "why": f"Price model: {part['pct']:+.0f}% on the estimate"})
    if estimate is None and area and card["sold"].get("vsPpsfPct") is not None:
        value = card["sold"]["ppsf12"] * sqft
        err, band = band_error(predictor, "ppsf", value) if predictor else (None, None)
        estimate = {"kind": "ppsf", "value": nice(value), "ppsf": card["sold"]["ppsf12"], "sqft": sqft, "error": err,
                    "errorBand": band}
    # the median ignores size, so it is offered only for the home type that dominates the area's sales
    if (estimate is None and area and card["sold"].get("vsMedianPct") is not None and home_type
            and home_type == area.get("topType")):
        estimate = {"kind": "median", "value": card["sold"]["median12"], "error": None}
    if estimate is None or not price:
        card["noEstimate"] = ("No asking price" if not price else no_estimate if no_estimate
                              else "Not placed in an area" if not area
                              else "No sold homes of this type nearby to compare" if home_type in NO_PPSF_TYPES
                              else "No floor area listed" if not sqft
                              else "No sold homes of this type nearby to compare")
    if estimate and price:
        e = estimate["error"]
        if e is not None and estimate["kind"] != "median":
            k = 1 + e / 100
            estimate.update(low=nice(estimate["value"] / k), high=nice(estimate["value"] * k))
        estimate["vsPct"] = round(100 * (price / estimate["value"] - 1), 1)
        card["estimate"] = estimate

    # description and photos
    if tag:
        card["signals"] = sorted(tag.get("signals") or {})
        p, m, t = signal_items(tag.get("signals"))
        plus += p
        minus += m
        notes += t
        photo, m = photo_summary(tag.get("photo")) if ctx["photoCity"] else (None, [])
        minus += m
        if photo:
            card["photo"] = photo
    card["plus"], card["minus"] = plus, minus
    if notes:
        card["tags"] = notes
    card["_detail"] = detail_for(card, row, tag, sale, ctx)
    return card


# ---------------------------------------------------------------------------- the detail panel (one small file per home)

COMPS = 4            # recent recorded sales shown beside a listing
COMP_KM = 1.0        # nearest first, within this distance; otherwise the area's latest sales


def detail_name(card_id):
    """A file name for a home's detail file: stable, URL-safe, and free of the address characters in the id."""
    return hashlib.sha1(card_id.encode("utf-8")).hexdigest()[:16]


class SoldIndex:
    """The recorded sales the site already publishes (<city>/sales.json, the latest months), for nearby comparisons."""

    def __init__(self, sales_doc, areas):
        cols = (sales_doc or {}).get("columns") or {}
        types = (sales_doc or {}).get("typeNames") or []
        col = lambda k, i: cols[k][i] if k in cols and i < len(cols[k]) else None
        self.rows = []
        for i, date in enumerate(cols.get("date") or []):
            a, t = col("area", i), col("type", i)
            if not col("price", i):
                continue
            self.rows.append({"date": date, "price": col("price", i), "address": col("address", i), "sqft": col("sqft", i),
                              "type": types[t] if isinstance(t, int) and t < len(types) else None,
                              "area": areas[a]["key"] if isinstance(a, int) and 0 <= a < len(areas) else None,
                              "lat": col("lat", i), "lon": col("lon", i), "key": (normalize_address(col("address", i), "") or ())[:3]})

    def near(self, lat, lon, area_key, home_type, address):
        """Up to COMPS sales: the nearest within COMP_KM (same home type first), then the area's latest."""
        own = (normalize_address(address, "") or ())[:3] if address else None
        pool = [r for r in self.rows if not own or r["key"] != own]
        picked = []
        if lat is not None and lon is not None:
            close = []
            for r in pool:
                if type(r["lat"]) is float and type(r["lon"]) is float and abs(r["lat"] - lat) < 0.02:
                    km = math.hypot(r["lat"] - lat, (r["lon"] - lon) * math.cos(math.radians(lat))) * 111.2
                    if km <= COMP_KM:
                        close.append((r["type"] != home_type, km, r))
            close.sort(key=lambda x: (x[0], x[1]))
            picked = [dict(r, km=round(km, 2)) for _, km, r in close[:COMPS]]
        if len(picked) < COMPS and area_key:
            have = {(r["address"], r["date"]) for r in picked}
            more = [r for r in pool if r["area"] == area_key and (r["address"], r["date"]) not in have
                    and (r["type"] == home_type or not home_type)]
            more.sort(key=lambda r: r["date"], reverse=True)
            picked += more[:COMPS - len(picked)]
        return [{k: r.get(k) for k in ("address", "price", "date", "sqft", "type", "km") if r.get(k) is not None} for r in picked]


def detail_for(card, row, tag, sale, ctx):
    """What only the detail panel needs: quoted description phrases, every finding with its crops, coverage and editing
    notes, this address's record, and nearby recorded sales. Asking and sold figures stay in separate fields."""
    out = {"id": card["id"]}
    signals = (tag or {}).get("signals") or {}
    if signals:
        out["quotes"] = [{"signal": k, "label": SIGNAL_ITEMS.get(k, (0, k.replace("_", " ")))[1], "quote": q}
                         for k, q in signals.items() if isinstance(q, str)]
    photo = (tag or {}).get("photo") if ctx["photoCity"] else None
    if photo:
        out["photo"] = {
            "findings": [{"type": f.get("type"), "label": finding_label(f), "item": f.get("item"),
                          "room": str(f.get("room") or "").replace("_", " "), "rooms": f.get("rooms") or 1,
                          "confidence": f.get("confidence"), "evidence": f.get("evidence"), "crops": f.get("crops") or []}
                         for f in photo.get("findings") or []],
            "missing": (photo.get("coverage") or {}).get("missing") or [],
            "coverage": (photo.get("coverage") or {}).get("rating"),
            "analysed": photo.get("photosAnalysed") or (photo.get("coverage") or {}).get("photosAnalysed"),
            "labelled": [x.get("text") for x in (photo.get("editing") or {}).get("labelled") or [] if x.get("text")],
            "possiblyEdited": bool((photo.get("editing") or {}).get("suspected") or (photo.get("editing") or {}).get("emptyAndFurnished")),
            "condition": photo.get("condition"), "credit": photo.get("credit"), "creditUrl": photo.get("creditUrl"),
        }
    record = {}
    if sale:
        yb = _to_int(sale.get("year_built"))
        if yb and 1700 <= yb <= int(ctx["today"][:4]) + 1:
            record["yearBuilt"] = yb
        assessed = _to_int(sale.get("assessed"))
        if assessed and assessed >= 10_000:
            record["assessed"] = assessed       # the city's assessed value on the stored record
    if record:
        out["record"] = record
    comps = ctx["sold"].near(card.get("lat"), card.get("lon"), (card.get("area") or {}).get("key"),
                             card["facts"].get("homeType"), card.get("address"))
    if comps:
        out["comps"] = comps
    return out


# ---------------------------------------------------------------------------- the city file

def _locator(store, city):
    cfg = CITY_CFG.get(city) or {}
    path = store.boundary_path(city) if store and cfg.get("boundaries") else None
    if not path or not os.path.exists(path):
        return None
    from .geo import Locator
    return Locator(path, cfg["boundaries"]["name"])


def sales_doc_overview(root, city):
    return read_json(root / city / "overview.json")


def city_figures(root, city):
    overview = read_json(root / city / "overview.json") or {}
    index = read_json(root / "index.json") or {}
    meta = next((c for c in index.get("cities") or [] if c.get("id") == city), {})
    growth = (overview.get("repeatIndex") or {}).get("growth1")
    if growth is None:
        growth = (meta.get("kpi") or {}).get("medianYoY")
    cx = meta.get("context") or {}
    return {"growth1": _num(growth), "violentRate": _num(cx.get("violentRate")), "propertyRate": _num(cx.get("propertyRate")),
            "ppsf": _num((meta.get("kpi") or {}).get("ppsf"))}


def build(root, city, store=None, now=None, listings_root=None):
    """The listing-cards document for one city, or None when the city has no merged listings."""
    root = Path(root)
    now = now or datetime.now(timezone.utc)
    listings = read_json(Path(listings_root or root) / city / "listings.json")
    if not listings or not isinstance(listings.get("rows"), list):
        return None
    areas_doc = read_json(root / city / "areas.json") or {}
    areas = [a for a in areas_doc.get("areas") or [] if isinstance(a, dict) and a.get("key")]
    tags_doc = read_json(root / city / "listing-tags.json") or {}
    tags = {}
    for t in tags_doc.get("listings") or []:
        for u in [t.get("url")] + list(t.get("urls") or []):
            if u:
                tags.setdefault(u, t)
    predictor = read_json(root / city / "model-predictor.json")
    sales_doc = read_json(root / city / "sales.json")
    parcels = None
    if store is not None and predictor:
        parcels = ParcelIndex(store, city)
    ctx = {"city": city, "today": now.date().isoformat(), "tags": tags, "predictor": predictor, "parcels": parcels,
           "locator": _locator(store, city), "areas_by_key": {a["key"]: a for a in areas},
           "areas_by_name": {**{a["key"].partition("|")[0]: a for a in areas}, **{a["name"]: a for a in areas}},
           "label": lambda key: area_label(CITY_CFG.get(city) or {"area_by": "area"}, key)[0],
           "index": AreaIndex(areas_doc, sales_doc_overview(root, city), sales_doc, city),
           "sold": SoldIndex(sales_doc, areas_doc.get("areas") or []),
           "cityFigures": city_figures(root, city), "photoCity": bool(tags_doc.get("photoTagging"))}
    rows = [r for r in listings["rows"] if isinstance(r, dict) and r.get("url")]
    seen = {u for r in rows for u in [r["url"]] + [s.get("url") for s in r.get("sources") or []]}
    # homes kept by the tag store past the 7-day snapshot (photo findings live 30 days) still get a card
    for t in tags_doc.get("listings") or []:
        if t.get("inSnapshot") is False and not set([t.get("url")] + list(t.get("urls") or [])) & seen:
            rows.append({"url": t["url"], "address": t.get("address"), "date": t.get("date"), "price": t.get("price"),
                         "source": t.get("source"), "inSnapshot": False})
    cards = [card_for(r, ctx) for r in rows]
    details = {}
    for c in cards:
        name = detail_name(c["id"])
        details[name] = c.pop("_detail")
        c["detail"] = name
    cards.sort(key=lambda c: (c.get("date") or "", c.get("price") or 0), reverse=True)
    models = [c for c in cards if (c.get("estimate") or {}).get("kind") == "model"]
    return {
        "city": city, "generatedAt": now.isoformat(timespec="seconds"), "asOf": listings.get("asOf"),
        "from": listings.get("from"), "through": listings.get("through"),
        "publishers": {k: {"source": v.get("source"), "asOf": v.get("asOf"), "rows": v.get("rows")}
                       for k, v in (listings.get("publishers") or {}).items()},
        "sold": {"through": areas_doc.get("through"), "basis": "recorded sales, 12 complete months"},
        "model": {"basis": predictor.get("basis"), "level": predictor.get("level", {}).get("quarters"),
                  "error": (predictor.get("error") or {}).get("model", {}).get("mdape"),
                  "ppsfError": (predictor.get("error") or {}).get("ppsf", {}).get("mdape")} if predictor else None,
        "cityFigures": ctx["cityFigures"], "photoTagging": ctx["photoCity"], "retentionDays": tags_doc.get("retentionDays"),
        "summary": {"cards": len(cards), "withPin": sum(1 for c in cards if "lat" in c),
                    "withSqft": sum(1 for c in cards if c["facts"].get("sqft")), "withArea": sum(1 for c in cards if c.get("area")),
                    "estimates": {k: sum(1 for c in cards if (c.get("estimate") or {}).get("kind") == k) for k in ("model", "ppsf", "median")},
                    "modelled": len(models), "withFindings": sum(1 for c in cards if (c.get("photo") or {}).get("findings"))},
        "cards": cards,
        "_details": details,
    }


def publish(root, city, store=None, now=None, log=print, listings_root=None):
    doc = build(root, city, store, now, listings_root)
    if doc is None:
        return None
    details = doc.pop("_details")
    folder = Path(root) / city / "listing-detail"
    for name, detail in details.items():
        path = folder / f"{name}.json"
        if read_json(path) != detail:          # unchanged files are left alone
            write_json(path, detail)
    for old in folder.glob("*.json") if folder.is_dir() else []:
        if old.stem not in details:
            old.unlink()
    write_json(Path(root) / city / "listing-cards.json", doc)
    s = doc["summary"]
    log(f"{city}: listing-cards.json, {s['cards']} homes ({s['withPin']} with a pin, {s['withArea']} in an area); "
        f"estimates {s['estimates']}")
    return doc


def refresh(root, cities=CITY_IDS, store_dir="store", log=print):
    """Rebuild the cards after listings or tags change. Never raises: a failure here must not lose the harvest."""
    from .store import Store
    store = Store(store_dir) if store_dir and os.path.isdir(store_dir) else None
    for city in cities:
        try:
            publish(root, city, store, log=log)
        except Exception as exc:   # noqa: BLE001  the cards are a derived view; the next run rebuilds them
            log(f"{city}: listing cards not rebuilt ({type(exc).__name__}: {exc})")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--city", choices=CITY_IDS)
    parser.add_argument("--output", type=Path, default=Path("web/public/data"))
    parser.add_argument("--store", default="store", help="harvest store (boundaries, parcel records); optional")
    parser.add_argument("--listings", type=Path, help="where <city>/listings.json is, if not under --output (the merged "
                                                       "file itself need not be published with the site)")
    args = parser.parse_args(argv)
    from .store import Store
    store = Store(args.store) if os.path.isdir(args.store) else None
    for city in [args.city] if args.city else CITY_IDS:
        if publish(args.output, city, store, listings_root=args.listings) is None:
            print(f"{city}: no listings.json, skipped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
