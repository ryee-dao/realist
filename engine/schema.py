"""One schema for every city, and the rules a record must pass to be kept."""
import datetime as dt
import heapq
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


INTAKE_SAMPLE = 60        # newest records read per source, kept as a sample for the pipeline page


class Normalizer:
    """Builds validated sale rows and counts why the rest were rejected.

    It also keeps a small sample of the newest records it judged (by sale date), with the real verdict, for the
    pipeline page. An adapter that rejects a row itself calls `peek` first so the sample can show that row too.
    Only date, price, address and home type are kept: never party names.
    """

    def __init__(self, today=None, sample=INTAKE_SAMPLE):
        self.today = today or dt.date.today()
        self.max_date = (self.today + dt.timedelta(days=31)).isoformat()
        self.rejected = Counter()
        self.seen = 0
        self._size = sample
        self._heap = []      # (date, order read, record): the newest `sample` records by sale date
        self._order = 0
        self._row = None

    def peek(self, *, date=None, price=None, address="", kind=""):
        """Describe the row about to be judged, so a rejection made outside `sale` can still be sampled."""
        self._row = {"date": str(date or "")[:10], "price": to_int(price), "address": clean_text(address), "kind": clean_text(kind)}

    def _note(self, row, verdict, reason=None):
        self._row = None
        if not self._size or not row:
            return
        date = row["date"]
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date) or not (MIN_DATE <= date <= self.max_date):
            return
        self._order += 1
        item = (date, self._order, {**row, "verdict": verdict, "reason": reason})
        if len(self._heap) < self._size:
            heapq.heappush(self._heap, item)
        elif item[:2] > self._heap[0][:2]:
            heapq.heapreplace(self._heap, item)

    def sample(self):
        """The sampled records, newest sale date first (ties: last read first)."""
        return [rec for _, _, rec in sorted(self._heap, key=lambda t: t[:2], reverse=True)]

    def reject(self, reason):
        self.seen += 1
        self.rejected[reason] += 1
        self._note(self._row, "reject", reason)

    def _drop(self, reason, row):
        self.rejected[reason] += 1
        self._note(row, "reject", reason)
        return None

    def sale(self, *, id, date, price, type, address="", zip="", area="", group="",
             sqft=None, year_built=None, beds=None, lat=None, lon=None, assessed=None, parcel=""):
        self.seen += 1
        peeked = self._row or {}
        row = {"date": str(date or "")[:10], "price": to_int(price),
               "address": clean_text(address) or peeked.get("address", ""), "kind": peeked.get("kind") or (type or "")}
        date = row["date"]
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date) or not (MIN_DATE <= date <= self.max_date):
            return self._drop("bad or missing sale date", row)
        price = to_int(price)
        if price is None or price < MIN_PRICE:
            return self._drop(f"nominal price (under ${MIN_PRICE:,})", row)
        if price > MAX_PRICE:
            return self._drop("price above $100M", row)
        if type not in TYPES:
            return self._drop("not a residential home type", row)
        if not id:
            return self._drop("no record id", row)
        self._note({**row, "kind": type}, "keep")
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
