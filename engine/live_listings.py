"""Refresh city listing snapshots from public publisher pages, reading one publisher per city per run.

Each run reads, for every city, the eligible publisher that was tried longest ago, and falls through to the
next one only when that read fails or finds nothing. A publisher that refuses a request (HTTP 403/429 or a
bot-check page) is skipped for a growing cool-down. Attempt, success and failure history is kept in
<output>/<city>/harvest-state.json. Each successful read is kept in sources/<provider>/<city>/new-listings.json,
and <city>/listings.json merges every publisher's latest snapshot into one row per home.
"""
import argparse
import http.client
import json
import os
import re
import sys
import time
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from html import unescape
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from .listing_sources import atproperties, coldwell_banker, everyhome, movoto, northrop, onekey, trulia, urban, weichert
from .listing_sources.browser import browser_loader
from .listing_sources.pages import CITIES, Blocked, InactiveListing, fetch, valid_price, zip_of

PROVIDERS = {"movoto": movoto, "weichert": weichert, "coldwell_banker": coldwell_banker, "everyhome": everyhome,
             "atproperties": atproperties, "onekey": onekey, "trulia": trulia, "northrop": northrop, "urban": urban}
# Paused for direct HTTP. Trulia is eligible when the browser transport is selected.
PAUSED = {"trulia": "answered HTTP 403 to every search page in November 2026"}
PAGE_LIMIT = 8
PROPERTY_LIMIT = 16
WINDOW_DAYS = 7
KEEP_DAYS = 180     # the merged file keeps a home until its list date is this old (or a publisher says it is off the market)
SLIM_DAYS = 30      # past this age a kept home drops its photo URLs and per-publisher copies of the description
COOL_DOWN_DAYS = (1, 3, 7)  # after the first, second and every later consecutive refusal
ATTENTION_FAILURES = 3
FACT_FIELDS = ("beds", "baths", "sqft", "type", "lat", "lon", "zip")
DETAIL_FIELDS = ("description", "photos") + FACT_FIELDS
ENTRY_FIELDS = ("url", "address", "date", "price", "source", "photos", "description", "summary", "broker") + FACT_FIELDS
SOURCE_ERRORS = (ValueError, KeyError, TypeError, AttributeError, OSError, http.client.HTTPException)


def window(city, now):
    today = now.astimezone(ZoneInfo(CITIES[city][2])).date()
    return (today - timedelta(days=WINDOW_DAYS - 1)).isoformat(), today.isoformat()


def read_properties(source, links, now, load, pause):
    """Rows from property pages (publishers that publish the list date only there), and the error that stopped them."""
    rows = []
    for url in links[:PROPERTY_LIMIT]:
        pause(getattr(source, "REQUEST_INTERVAL", 1))
        try:
            row = source.property_row(load(url), url)
        except SOURCE_ERRORS as exc:
            print(f"{source.NAME} property page unavailable: {exc}", file=sys.stderr)
            if getattr(exc, "code", None) == 404:
                continue
            return rows, exc
        if row:
            row["detailAt"] = now.isoformat()
            rows.append(row)
    return rows, None


def read_source(source, city, now, load, pause):
    zone = ZoneInfo(CITIES[city][2])
    earliest, today = window(city, now)
    limit = min(getattr(source, "MAX_PAGES", PAGE_LIMIT), PAGE_LIMIT)
    rows, pages, error = {}, 0, None
    partial, interrupted = getattr(source, "SAMPLE", False), False
    url = source.search_url(city, 1)
    for number in range(1, limit + 1):
        links = None
        try:
            page = load(url)
            if hasattr(source, "detail_links"):
                links = list(dict.fromkeys(source.detail_links(page, city)))
                candidates, error = read_properties(source, links, now, load, pause)
            else:
                candidates = source.search(page, city, zone)
        except SOURCE_ERRORS as exc:
            if not pages:
                raise
            print(f"{city}: {source.NAME} page {number}: {exc}", file=sys.stderr)
            error, candidates = exc, []
        pages += 1
        for row in candidates:
            if earliest <= row["date"] <= today and valid_price(row["price"]):
                rows.setdefault(row["url"], row)
        if error is not None:
            interrupted = partial = True
            break
        if links:  # one results page with listings is sampled through its property pages
            partial = True
            break
        next_url = source.search_url(city, number + 1)
        if next_url is None or next_url == url:
            break
        if getattr(source, "LINKED_PAGES", True) and next_url not in page.links and urlsplit(next_url).path not in page.links:
            break
        if number == limit:
            partial = True
            break
        url = next_url
        pause(1)
    return {"source": source.NAME, "pages": pages, "partial": partial, "interrupted": interrupted, "error": error,
            "rows": sorted(rows.values(), key=lambda r: r["date"], reverse=True)}


def paused_publishers():
    if os.environ.get("REALIST_LISTING_TRANSPORT") == "patchright":
        return {key: reason for key, reason in PAUSED.items() if key != "trulia"}
    return PAUSED


def eligible(city):
    return [key for key, source in PROVIDERS.items() if city in getattr(source, "PATHS", CITIES) and key not in paused_publishers()]


def rotation(city, publishers, now, requested=None):
    """Publishers to try this run: the least recently attempted first, leaving out those cooling down."""
    if requested:
        return [requested]
    names = eligible(city)
    ready = [k for k in names if ((publishers.get(k) or {}).get("coolUntil") or "") <= now.isoformat()]
    return sorted(ready, key=lambda k: ((publishers.get(k) or {}).get("lastAttemptAt") or "", names.index(k)))


def note_success(record, now, rows):
    record.update(lastSuccessAt=now.isoformat(), lastRows=rows, failures=0, blocks=0, coolUntil=None, lastError=None)


def note_failure(record, now, exc):
    record["failures"] = record.get("failures", 0) + 1
    record["lastError"] = str(exc)[:300]
    if isinstance(exc, Blocked):
        record["blocks"] = record.get("blocks", 0) + 1
        days = COOL_DOWN_DAYS[min(record["blocks"], len(COOL_DOWN_DAYS)) - 1]
        record["coolUntil"] = (now + timedelta(days=days)).isoformat()


def collect(city, now, publishers, previous, requested=None, load=fetch, pause=time.sleep):
    best = None
    for key in rotation(city, publishers, now, requested):
        record = publishers.setdefault(key, {})
        record["lastAttemptAt"] = now.isoformat()
        try:
            candidate = read_source(PROVIDERS[key], city, now, load, pause)
        except SOURCE_ERRORS as exc:
            print(f"{city}: {key} unavailable: {exc}", file=sys.stderr)
            note_failure(record, now, exc)
            continue
        candidate["provider"] = key
        error = candidate.pop("error")
        if candidate["rows"] and not candidate["interrupted"]:
            note_success(record, now, len(candidate["rows"]))
            best = candidate
            break
        note_failure(record, now, error or ValueError(f"no listings dated in the last {WINDOW_DAYS} days"))
        if best is None or len(candidate["rows"]) > len(best["rows"]):
            best = candidate
    if best is None or not best["rows"]:
        if previous.get("status") == "ready" and (not requested or previous.get("provider") == requested):
            return {**previous, "stale": True}
        return {"status": "unavailable"}
    best.pop("interrupted")
    return {**best, "status": "ready", "asOf": now.isoformat(), "stale": False}


def enrich(snapshot, previous, now, limit, load=fetch, pause=time.sleep, inactive=None):
    """Add property-page details to rows that lack them; returns the refusal that stopped it, if any.

    A property page that says the listing is no longer for sale removes the row, and its URL is added to `inactive`."""
    source = PROVIDERS[snapshot["provider"]]
    cached = {row["url"]: row for row in previous.get("rows", [])}
    pending = []
    for row in snapshot["rows"]:
        if row.get("detailAt"):
            continue
        old = cached.get(row["url"], {})
        if old.get("date") == row["date"] and old.get("price") == row["price"]:
            for key in DETAIL_FIELDS + ("detailAt", "detailAttemptAt"):
                if old.get(key) not in (None, "", []) and (key != "photos" or len(old[key]) >= len(row.get(key) or [])):
                    row[key] = old[key]
        if row.get("detailAt", "") < (now - timedelta(hours=12)).isoformat():
            pending.append(row)
    for row in sorted(pending, key=lambda r: r.get("detailAttemptAt", ""))[:limit]:
        row["detailAttemptAt"] = now.isoformat()
        try:
            pause(1)
            detail = source.detail(load(row["url"]), row["url"])
            row.update({k: v for k, v in detail.items() if k in DETAIL_FIELDS and v not in (None, "", [])})
            row["detailAt"] = now.isoformat()
            if detail.get("galleryUrl"):
                pause(1)
                row["photos"] = source.gallery(load(detail["galleryUrl"])) or row["photos"]
        except InactiveListing:
            snapshot["rows"].remove(row)
            if inactive is not None:
                inactive.append(row["url"])
        except SOURCE_ERRORS as exc:
            print(f"{source.NAME} detail unavailable: {exc}", file=sys.stderr)
            if isinstance(exc, Blocked):
                return exc
    return None


def attention(city, publishers, now):
    """Publishers the owner should look at: paused, cooling down after a refusal, or failing repeatedly."""
    notes = [{"provider": key, "paused": reason} for key, reason in paused_publishers().items()
             if city in getattr(PROVIDERS[key], "PATHS", CITIES)]
    for key in eligible(city):
        record = publishers.get(key) or {}
        cooling = (record.get("coolUntil") or "") > now.isoformat()
        if cooling or record.get("failures", 0) >= ATTENTION_FAILURES:
            notes.append({"provider": key, "failures": record.get("failures", 0), "coolUntil": record.get("coolUntil") if cooling else None,
                          "lastSuccessAt": record.get("lastSuccessAt"), "lastError": record.get("lastError")})
    return notes


# ---- one row per home across publishers ----------------------------------------------------------------------

SUFFIXES = {"STREET": "ST", "ST": "ST", "AVENUE": "AVE", "AVE": "AVE", "AV": "AVE", "ROAD": "RD", "RD": "RD",
            "DRIVE": "DR", "DR": "DR", "PLACE": "PL", "PL": "PL", "BOULEVARD": "BLVD", "BLVD": "BLVD", "COURT": "CT",
            "CT": "CT", "LANE": "LN", "LN": "LN", "TERRACE": "TER", "TER": "TER", "TERR": "TER", "PARKWAY": "PKWY",
            "PKWY": "PKWY", "SQUARE": "SQ", "SQ": "SQ", "CIRCLE": "CIR", "CIR": "CIR", "HIGHWAY": "HWY", "HWY": "HWY",
            "PLAZA": "PLZ", "PLZ": "PLZ", "WAY": "WAY", "PIKE": "PIKE", "ALLEY": "ALY", "ALY": "ALY", "WALK": "WALK",
            "ROW": "ROW", "PARK": "PARK", "LOOP": "LOOP", "TRAIL": "TRL", "TRL": "TRL", "TURNPIKE": "TPKE", "TPKE": "TPKE",
            "EXPRESSWAY": "EXPY", "EXPY": "EXPY", "CRESCENT": "CRES", "CRES": "CRES", "MEWS": "MEWS"}
DIRECTIONS = {"NORTH": "N", "SOUTH": "S", "EAST": "E", "WEST": "W", "N": "N", "S": "S", "E": "E", "W": "W",
              "NORTHEAST": "NE", "NORTHWEST": "NW", "SOUTHEAST": "SE", "SOUTHWEST": "SW", "NE": "NE", "NW": "NW", "SE": "SE", "SW": "SW"}
UNIT_WORDS = {"APT", "APARTMENT", "UNIT", "#", "STE", "SUITE", "FL", "FLOOR", "RM", "ROOM"}


def home_key(address, zip_code=None):
    """A publisher-independent key for a street address: number, street, unit and ZIP code.

    "220 S Green Street #4S, Chicago, IL 60607" and "220 South Green St Unit 4S Chicago, IL 60607" share a key.
    Queens-style house numbers ("84-27") are joined; a Philadelphia-style range ("2429-31") keeps its first number.
    """
    if not isinstance(address, str) or not address.strip():
        return None
    text = unescape(address).upper()
    zip_code = zip_code or zip_of(text)
    head = re.sub(r"[^\w#\s-]", " ", text.split(",")[0]).replace("#", " # ")
    tokens = head.split()
    if not tokens or not re.match(r"\d", tokens[0]):
        return None
    queens = re.fullmatch(r"(\d{1,3})-(\d{1,3}[A-Z]?)", tokens[0])
    number = queens[1] + queens[2] if queens else re.match(r"\d+[A-Z]?", tokens[0])[0]
    rest = tokens[1:]
    # The street ends at its last suffix word before any unit word; a Movoto-style city name may follow it.
    stop = next((i for i, t in enumerate(rest) if t in UNIT_WORDS), len(rest))
    ends = [i for i in range(1, stop) if rest[i] in SUFFIXES]
    end = ends[-1] + 1 if ends else stop
    if ends and end < stop and rest[end] in DIRECTIONS:
        end += 1
    street = [SUFFIXES[t] if ends and i == ends[-1] else re.sub(r"^(\d+)(?:ST|ND|RD|TH)$", r"\1", DIRECTIONS.get(t, t))
              for i, t in enumerate(rest[:end])]
    if stop < len(rest):
        unit = next((t for t in rest[stop + 1:] if t not in UNIT_WORDS), None)
    elif end < len(rest) and re.search(r"\d", rest[end]) and len(rest[end]) <= 6:
        unit = rest[end]  # an unlabelled unit after the street: "2429 N Front St 31"
    else:
        unit = None
    if not street:
        return None
    unit = re.sub(r"[^A-Z0-9]", "", unit or "").lstrip("0") or None
    return " ".join([number] + street) + (f" #{unit}" if unit else "") + (f" {zip_code}" if zip_code else "")


def same_price(a, b):
    return type(a) in (int, float) and type(b) in (int, float) and abs(a - b) <= 0.005 * max(a, b)


def base_key(key):
    return str(key).split(" ~")[0]


def merge_home(key, entries):
    entries.sort(key=lambda e: e["asOf"], reverse=True)  # the most recent read first
    latest = entries[0]
    gallery = max(entries, key=lambda e: len(e.get("photos") or []))
    text = max(entries, key=lambda e: len(e.get("description") or ""))
    row = {"key": key, "url": latest["url"], "address": latest["address"], "date": min(e["date"] for e in entries),
           "price": latest["price"], "source": latest["source"], "photos": gallery.get("photos") or [],
           "description": text.get("description"), "summary": next((e["summary"] for e in entries if e.get("summary")), None),
           "broker": next((e["broker"] for e in entries if e.get("broker")), None)}
    for field in FACT_FIELDS:
        value = next((e[field] for e in entries if e.get(field) is not None), None)
        if value is not None:
            row[field] = value
    row["sources"] = entries
    return row


def merge_city(root, city, now, inactive=()):
    """One row per home: every publisher's latest snapshot read within the last WINDOW_DAYS, plus every home kept from
    the previous merged file, so homes accumulate while they are on the market.

    Each run reads one publisher's newest pages, so an older home simply stops being seen; that alone never drops it.
    Each row carries `firstSeen` and `lastSeen` (when a publisher's read last listed it). A home is dropped when its
    list date is over KEEP_DAYS old, or when a property page said it is no longer for sale (`inactive` URLs, kept in
    `offMarket` so another publisher's older snapshot does not bring it back). Homes listed over SLIM_DAYS ago lose
    their photo URLs and per-publisher descriptions to keep the file small.
    """
    _, today = window(city, now)
    earliest = (datetime.fromisoformat(today) - timedelta(days=KEEP_DAYS)).date().isoformat()
    slim_before = (datetime.fromisoformat(today) - timedelta(days=SLIM_DAYS)).date().isoformat()
    previous = read_snapshot(root / city / "listings.json")
    off = {u: d for u, d in (previous.get("offMarket") or {}).items() if isinstance(d, str) and d >= earliest}
    off.update({u: today for u in inactive})
    cutoff = (now - timedelta(days=WINDOW_DAYS)).isoformat()
    homes, publishers = {}, {}
    for path in sorted((root / "sources").glob(f"*/{city}/new-listings.json")):
        snapshot, provider = read_snapshot(path), path.parts[-3]
        if snapshot.get("status") != "ready" or (snapshot.get("asOf") or "") < cutoff:
            continue
        publishers[provider] = {"source": snapshot.get("source"), "asOf": snapshot["asOf"], "partial": snapshot.get("partial"), "rows": 0}
        for row in snapshot.get("rows") or []:
            if not (isinstance(row, dict) and row.get("url") and earliest <= (row.get("date") or "") <= today and valid_price(row.get("price"))):
                continue
            entry = {"provider": provider, "asOf": snapshot["asOf"],
                     **{k: row[k] for k in ENTRY_FIELDS if row.get(k) not in (None, "", [])}}
            key = home_key(row.get("address"), row.get("zip")) or row["url"]
            # Two URLs from one publisher at one address are two homes only when their prices differ (units listed
            # without a unit number). At the same price it is one home under two links (a publisher's search and
            # property pages can link it differently), and the entry with the larger gallery is kept.
            slot, n, twin = key, 1, None
            while True:
                mine = [e for e in homes.get(slot, []) if e["provider"] == provider]
                twin = next((e for e in mine if same_price(e.get("price"), entry.get("price"))), None)
                if twin is not None or not mine:
                    break
                n += 1
                slot = f"{key} ~{n}"
            if twin is not None:
                if len(entry.get("photos") or []) > len(twin.get("photos") or []):
                    homes[slot][homes[slot].index(twin)] = entry
                continue
            homes.setdefault(slot, []).append(entry)
            publishers[provider]["rows"] += 1
    fresh = [merge_home(key, entries) for key, entries in homes.items()]
    rows = carry_forward(fresh, previous.get("rows") or [], off, earliest)
    for r in rows:
        if r["date"] < slim_before:
            slim(r)
    rows.sort(key=lambda r: (r["date"], r.get("address") or ""), reverse=True)
    return {"status": "ready", "city": city, "asOf": now.isoformat(),
            "from": min((r["date"] for r in rows), default=today), "through": today, "keepDays": KEEP_DAYS,
            "publishers": publishers, "entries": sum(len(r["sources"]) for r in rows), "homes": len(rows),
            "seenThisWeek": len(fresh), "offMarket": off, "rows": rows}


def _urls(row):
    return {row.get("url")} | {e.get("url") for e in row.get("sources") or [] if isinstance(e, dict)}


def carry_forward(fresh, previous, off, earliest):
    """Fresh rows get firstSeen/lastSeen; earlier rows not read again are kept unless too old or off the market."""
    by_key, by_url = {}, {}
    for old in previous:
        if isinstance(old, dict) and old.get("key"):
            by_key[old["key"]] = old
            for u in _urls(old):
                by_url[u] = old
    out, used = [], set()
    for row in fresh:
        if _urls(row) & set(off):
            continue
        seen = [e["asOf"] for e in row["sources"] if e.get("asOf")]
        old = by_key.get(row["key"]) or next((by_url[u] for u in _urls(row) if u in by_url), None)
        if old is not None:
            used.add(id(old))
            row["date"] = min(row["date"], old.get("date") or row["date"])
        row["firstSeen"] = min([s for s in [(old or {}).get("firstSeen")] + seen if s])
        row["lastSeen"] = max(seen + [(old or {}).get("lastSeen") or ""])
        out.append(row)
    keys = {r["key"] for r in out}
    by_base = {}
    for r in out:
        by_base.setdefault(base_key(r["key"]), []).append(r)
    for old in previous:
        if (not isinstance(old, dict) or id(old) in used or old.get("key") in keys or not old.get("url")
                or (old.get("date") or "") < earliest or _urls(old) & set(off)):
            continue
        # an earlier row for an address already kept at the same price is that home under another link: fold it in
        twin = next((r for r in by_base.get(base_key(old["key"]), []) if same_price(r.get("price"), old.get("price"))), None)
        if twin is not None:
            twin["date"] = min(twin["date"], old.get("date") or twin["date"])
            twin["firstSeen"] = min([x for x in (twin.get("firstSeen"), old.get("firstSeen")) if x], default=None)
            if len(old.get("photos") or []) > len(twin.get("photos") or []):
                twin["photos"] = old["photos"]
            if len(old.get("description") or "") > len(twin.get("description") or ""):
                twin["description"] = old["description"]
            have = _urls(twin)
            twin["sources"] = twin.get("sources", []) + [e for e in old.get("sources") or []
                                                         if isinstance(e, dict) and e.get("url") not in have]
            continue
        seen = [e.get("asOf") for e in old.get("sources") or [] if isinstance(e, dict) and e.get("asOf")]
        old.setdefault("firstSeen", min(seen) if seen else None)
        old.setdefault("lastSeen", max(seen) if seen else None)
        out.append(old)
        by_base.setdefault(base_key(old["key"]), []).append(old)
    return out


def slim(row):
    """Drop what an older home no longer needs: photo URLs and the per-publisher photo lists and descriptions."""
    row.pop("photos", None)
    for e in row.get("sources") or []:
        if isinstance(e, dict):
            e.pop("photos", None)
            e.pop("description", None)


# ---- files ---------------------------------------------------------------------------------------------------

def read_snapshot(path):
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def write_snapshot(path, snapshot):
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("w", dir=path.parent, delete=False) as tmp:
        json.dump(snapshot, tmp, separators=(",", ":"), allow_nan=False)
    os.replace(tmp.name, path)


def publish(root, city, requested, details, now=None, load=fetch, pause=time.sleep):
    now = now or datetime.now(timezone.utc)
    target, state_path = root / city / "new-listings.json", root / city / "harvest-state.json"
    publishers = read_snapshot(state_path).get("publishers") or {}
    snapshot = collect(city, now, publishers, read_snapshot(target), requested, load, pause)
    fresh = snapshot["status"] == "ready" and not snapshot["stale"]
    inactive = []
    if fresh:
        per_source = root / "sources" / snapshot["provider"] / city / "new-listings.json"
        refused = enrich(snapshot, read_snapshot(per_source), now, details, load, pause, inactive)
        if refused:
            note_failure(publishers[snapshot["provider"]], now, refused)
        write_snapshot(per_source, snapshot)
    notes = attention(city, publishers, now)
    write_snapshot(target, {**snapshot, "attention": notes})
    write_snapshot(state_path, {"updatedAt": now.isoformat(), "publishers": publishers, "attention": notes})
    merged = merge_city(root, city, now, inactive)
    write_snapshot(root / city / "listings.json", merged)
    for note in notes:
        prefix = "::warning::" if os.environ.get("GITHUB_ACTIONS") else "attention: "
        print(f"{prefix}{city}: {note['provider']} " + ", ".join(f"{k}={v}" for k, v in note.items() if k != "provider" and v),
              file=sys.stderr)
    print(f"{city}: {snapshot.get('source', 'unavailable')}, {len(snapshot.get('rows', []))} listings"
          f"{' (stale)' if snapshot.get('stale') else ''}; merged {merged['homes']} homes from {merged['entries']} rows"
          f" of {len(merged['publishers'])} publishers", flush=True)
    return fresh


# ---- seed: carry homes already harvested elsewhere into the hosted store ----------------------------------------

# Only what the site already shows about a home. No description, summary, broker text or photo URLs: the seed is
# stored on a public branch.
SEED_FIELDS = ("key", "url", "address", "price", "date", "beds", "baths", "sqft", "type", "lat", "lon", "zip",
               "firstSeen", "lastSeen")
SEED_SOURCE_FIELDS = ("provider", "source", "url", "asOf")


def seed_row(row):
    out = {k: row[k] for k in SEED_FIELDS if row.get(k) not in (None, "", [])}
    out["source"] = row.get("source")
    out["sources"] = [{k: e[k] for k in SEED_SOURCE_FIELDS if e.get(k)} for e in row.get("sources") or [] if isinstance(e, dict) and e.get("url")]
    if not out.get("lastSeen"):
        seen = [e["asOf"] for e in out["sources"] if e.get("asOf")]
        if seen:
            out["lastSeen"] = max(seen)
            out.setdefault("firstSeen", min(seen))
    return out


def export_seed(root, out_dir, now=None, cities=CITIES):
    """Write <out_dir>/<city>.json: the merged homes still within KEEP_DAYS, slimmed to SEED_FIELDS. Returns counts."""
    now = now or datetime.now(timezone.utc)
    counts = {}
    for city in cities:
        merged = read_snapshot(Path(root) / city / "listings.json")
        _, today = window(city, now)
        earliest = (datetime.fromisoformat(today) - timedelta(days=KEEP_DAYS)).date().isoformat()
        rows = [seed_row(r) for r in merged.get("rows") or []
                if isinstance(r, dict) and r.get("url") and r.get("key") and (r.get("date") or "") >= earliest]
        if not rows:
            continue
        write_snapshot(Path(out_dir) / f"{city}.json", {"city": city, "exportedAt": now.isoformat(), "keepDays": KEEP_DAYS,
                                                       "fields": list(SEED_FIELDS), "homes": len(rows), "rows": rows})
        counts[city] = len(rows)
    return counts


def import_seed(seed_dir, root, now=None, cities=CITIES):
    """Add seed homes the merged file does not already hold (same key or a shared URL), marked `seeded`.

    A home the harvester has is never overwritten, homes past KEEP_DAYS or reported off the market are skipped, and
    importing twice adds nothing. A later read of the same home replaces the stub (merge_city's carry_forward)."""
    now = now or datetime.now(timezone.utc)
    added = {}
    for city in cities:
        seed = read_snapshot(Path(seed_dir) / f"{city}.json")
        if not seed.get("rows"):
            continue
        path = Path(root) / city / "listings.json"
        merged = read_snapshot(path) or {"status": "ready", "city": city, "asOf": now.isoformat(), "publishers": {}, "rows": []}
        rows = merged.setdefault("rows", [])
        _, today = window(city, now)
        earliest = (datetime.fromisoformat(today) - timedelta(days=KEEP_DAYS)).date().isoformat()
        keys = {r.get("key") for r in rows if isinstance(r, dict)}
        urls = set().union(*(_urls(r) for r in rows if isinstance(r, dict))) | set(merged.get("offMarket") or {})
        n = 0
        for r in seed["rows"]:
            if (not isinstance(r, dict) or not r.get("url") or not r.get("key") or (r.get("date") or "") < earliest
                    or not valid_price(r.get("price")) or r["key"] in keys or _urls(r) & urls):
                continue
            rows.append({**{k: r[k] for k in SEED_FIELDS + ("source",) if r.get(k) is not None},
                         "sources": [e for e in r.get("sources") or [] if isinstance(e, dict)], "seeded": True})
            keys.add(r["key"])
            urls |= _urls(r)
            n += 1
        if n:
            rows.sort(key=lambda x: (x.get("date") or "", x.get("address") or ""), reverse=True)
            merged.update(homes=len(rows), entries=sum(len(x.get("sources") or []) for x in rows),
                          seeded=sum(1 for x in rows if x.get("seeded")))
            write_snapshot(path, merged)
        added[city] = n
    return added


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--export-seed", type=Path, metavar="DIR", help="write a slimmed seed of the merged listings and stop")
    parser.add_argument("--import-seed", type=Path, metavar="DIR", help="add a seed's homes to the merged listings and stop")
    parser.add_argument("--city", choices=CITIES)
    parser.add_argument("--source", choices=PROVIDERS, help="read this publisher now, ignoring the rotation and cool-downs")
    parser.add_argument("--details", default=20, type=int, help="maximum extra property pages per city, per run")
    parser.add_argument("--output", type=Path, default=Path("web/public/data"))
    args = parser.parse_args()
    if args.export_seed or args.import_seed:
        chosen = [args.city] if args.city else list(CITIES)
        if args.export_seed:
            print(f"seed written to {args.export_seed}: {export_seed(args.output, args.export_seed, cities=chosen)}")
        else:
            print(f"seed homes added: {import_seed(args.import_seed, args.output, cities=chosen)}")
        sys.exit(0)
    if args.details < 0:
        parser.error("--details must be nonnegative")
    supported = getattr(PROVIDERS.get(args.source), "PATHS", CITIES)
    if args.city and args.city not in supported:
        parser.error(f"{args.source} does not cover {args.city}")
    transport = os.environ.get("REALIST_LISTING_TRANSPORT", "http")
    if transport not in ("http", "patchright"):
        parser.error("REALIST_LISTING_TRANSPORT must be http or patchright")
    print(f"Listing transport: {transport}", flush=True)
    with (browser_loader() if transport == "patchright" else nullcontext(fetch)) as load:
        results = [publish(args.output, c, args.source, args.details, load=load) for c in ([args.city] if args.city else supported)]
    from . import listing_cards   # rebuild the New listings page's cards from the merged files (never fails the run)
    listing_cards.refresh(args.output, [args.city] if args.city else supported)
    sys.exit(0 if all(results) else 1)
