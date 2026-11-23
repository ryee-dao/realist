"""One schema for every city, and the rules a record must pass to be kept."""
import datetime as dt
import re
from collections import Counter

COLUMNS = [
    "id", "date", "price", "type", "address", "zip", "area", "group",
    "sqft", "year_built", "beds", "lat", "lon", "assessed", "parcel",
]

SINGLE = "Single family"
CONDO = "Condo"
COOP = "Co-op"
MULTI = "Multi-family"
TYPES = [SINGLE, CONDO, COOP, MULTI]

MIN_PRICE = 10_000        # below this a deed is a nominal transfer, not a market sale
MAX_PRICE = 100_000_000
MIN_DATE = "2000-01-01"

_SMALL = {"Of", "The", "And", "At", "In"}


def to_int(value):
    if value in (None, ""):
        return None
    try:
        return int(float(str(value).replace(",", "").replace("$", "").strip()))
    except ValueError:
        return None


def to_float(value):
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def clean_text(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def title(value):
    words = clean_text(value).title().split(" ")
    out = [w.lower() if i and w in _SMALL else w for i, w in enumerate(words)]
    return re.sub(r"(\d)(St|Nd|Rd|Th)\b", lambda m: m.group(1) + m.group(2).lower(), " ".join(out))


class Normalizer:
    """Builds validated sale rows and counts why the rest were rejected."""

    def __init__(self, today=None):
        self.today = today or dt.date.today()
        self.max_date = (self.today + dt.timedelta(days=31)).isoformat()
        self.rejected = Counter()
        self.seen = 0

    def reject(self, reason):
        self.seen += 1
        self.rejected[reason] += 1

    def sale(self, *, id, date, price, type, address="", zip="", area="", group="",
             sqft=None, year_built=None, beds=None, lat=None, lon=None, assessed=None, parcel=""):
        self.seen += 1
        date = str(date or "")[:10]
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date) or not (MIN_DATE <= date <= self.max_date):
            self.rejected["bad or missing sale date"] += 1
            return None
        price = to_int(price)
        if price is None or price < MIN_PRICE:
            self.rejected[f"nominal price (under ${MIN_PRICE:,})"] += 1
            return None
        if price > MAX_PRICE:
            self.rejected["price above $100M"] += 1
            return None
        if type not in TYPES:
            self.rejected["not a residential home type"] += 1
            return None
        if not id:
            self.rejected["no record id"] += 1
            return None
        sqft = to_int(sqft)
        if sqft is not None and not (200 <= sqft <= 20_000):
            sqft = None
        year_built = to_int(year_built)
        if year_built is not None and not (1700 <= year_built <= self.today.year + 1):
            year_built = None
        beds = to_int(beds)
        if beds is not None and not (0 < beds <= 20):
            beds = None
        lat, lon = to_float(lat), to_float(lon)
        if lat is None or lon is None or not (24 < lat < 50 and -125 < lon < -66):
            lat = lon = None
        assessed = to_int(assessed)
        zip = clean_text(zip)[:5]
        if not re.fullmatch(r"\d{5}", zip) or zip == "00000":
            zip = ""
        return {
            "id": str(id), "date": date, "price": price, "type": type,
            "address": clean_text(address), "zip": zip,
            "area": clean_text(area), "group": clean_text(group),
            "sqft": sqft, "year_built": year_built, "beds": beds,
            "lat": round(lat, 5) if lat is not None else None,
            "lon": round(lon, 5) if lon is not None else None,
            "assessed": assessed if assessed and assessed > 0 else None,
            "parcel": clean_text(parcel),
        }
