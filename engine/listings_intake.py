"""<city>/listings-intake.json for the pipeline page: the newest homes the listing harvest took in, and what it did with
each, plus where each publisher stands. Standard library only; written by engine/listing_cards.py next to the cards.

Reads the merged <city>/listings.json and <city>/harvest-state.json from the listings folder (engine/live_listings.py)
and, for homes a property page said are no longer for sale, <city>/listing-tags.json from the site folder (the only
stored place an address for such a home can still be). Facts only: list date, asking price, address, home type,
publisher names and the listing's public links. Never descriptions, photos or broker names.

Verdicts ("tag" says which):
  keep / new      one publisher lists the home; the harvest added it as a home of its own
  keep / merged   several publishers list the same home; their listings were merged into one
  keep / price    two reads of the home disagree on the asking price; the newest one is kept ("was" holds the other)
  keep / unseen   no publisher has listed the home for UNSEEN_DAYS; it is kept, but may be off the market
  drop / off      a property page said the home is no longer for sale; it was taken out (shown only when an address
                  is still on file; `offMarket.count` counts them all)
"""
import json
import re
from datetime import datetime, timedelta
from pathlib import Path

from .listing_analysis import infer_type
from .live_listings import CITIES as LISTING_CITIES, PROVIDERS, paused_publishers, same_price

RECORDS = 60
UNSEEN_DAYS = 14


def _read(path):
    try:
        data = json.loads(Path(path).read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _when(iso):
    try:
        return datetime.fromisoformat(str(iso))
    except (TypeError, ValueError):
        return None


def money(v):
    if type(v) not in (int, float):
        return "–"
    if v >= 1_000_000:
        return f"${v / 1_000_000:.2f}".rstrip("0").rstrip(".") + "M"
    return f"${round(v / 1000):,}k"


def _code(error):
    m = re.search(r"HTTP (\d{3})", str(error or ""))
    return int(m.group(1)) if m else None


def _name(key, fallback=None):
    return getattr(PROVIDERS.get(key), "NAME", None) or fallback or key


def publisher_status(city, listings, state, now):
    """One entry per publisher that covers the city: name, last read, rows, and state
    (ok, partial, refused, failed, paused, idle)."""
    read = listings.get("publishers") or {}
    records = (state.get("publishers") or {}) if isinstance(state.get("publishers"), dict) else {}
    paused = paused_publishers()
    out = []
    for key, source in PROVIDERS.items():
        if city not in getattr(source, "PATHS", LISTING_CITIES) and key not in read:
            continue
        got = read.get(key) if isinstance(read.get(key), dict) else {}
        rec = records.get(key) if isinstance(records.get(key), dict) else {}
        entry = {"provider": key, "name": _name(key, got.get("source")), "readAt": got.get("asOf") or rec.get("lastSuccessAt"),
                 "rows": got.get("rows") if got else rec.get("lastRows")}
        cool = rec.get("coolUntil") or ""
        last_try, last_ok = rec.get("lastAttemptAt") or "", rec.get("lastSuccessAt") or ""
        if key in paused and not got:
            entry["state"] = "paused"
            entry["code"] = _code(paused[key])
        elif cool and cool > now.isoformat():
            entry.update(state="refused", code=_code(rec.get("lastError")), until=cool)
        elif rec.get("failures") and last_try > last_ok:
            entry.update(state="failed", code=_code(rec.get("lastError")))
        elif got:
            entry["state"] = "partial" if got.get("partial") else "ok"
        else:
            entry["state"] = "idle"     # not read in the last week
        if isinstance(got.get("skipped"), dict):
            entry["skipped"] = {k: v for k, v in got["skipped"].items() if type(v) is int}
        out.append({k: v for k, v in entry.items() if v is not None})
    out.sort(key=lambda e: (e.get("readAt") or "", e["name"]), reverse=True)
    return out


def _publishers(row):
    names = []
    for e in row.get("sources") or []:
        if isinstance(e, dict):
            n = _name(e.get("provider"), e.get("source"))
            if n not in names:
                names.append(n)
    if not names and row.get("source"):
        names.append(row["source"])
    return names


def _links(row):
    urls = [row.get("url")] + [e.get("url") for e in row.get("sources") or [] if isinstance(e, dict)]
    return list(dict.fromkeys(u for u in urls if isinstance(u, str) and u.startswith("http")))


def record_for(row, city, read_at):
    entries = sorted((e for e in row.get("sources") or [] if isinstance(e, dict)), key=lambda e: e.get("asOf") or "", reverse=True)
    names = _publishers(row)
    was = next((e.get("price") for e in entries if type(e.get("price")) in (int, float)
                and not same_price(e.get("price"), row.get("price"))), None)
    last = _when(row.get("lastSeen"))
    unseen = (read_at - last).days if read_at and last and read_at - last >= timedelta(days=UNSEEN_DAYS) else None
    parts = []
    if unseen is not None:
        tag = "unseen"
        parts.append(f"may be off the market, not seen for {unseen} days")
    elif was is not None:
        tag = "price"
        parts.append(f"price changed, was {money(was)}")
    else:
        tag = "merged" if len(names) > 1 else "new"
    if len(names) > 1:
        parts.append(f"merged · {len(names)} sites")
    elif tag == "new":
        parts.append("new")
    out = {"date": row.get("date"), "price": row.get("price") if type(row.get("price")) in (int, float) else None,
           "address": row.get("address") or None, "type": infer_type(row, city), "verdict": "keep", "tag": tag,
           "reason": " · ".join(parts), "publishers": names, "links": _links(row),
           "firstSeen": row.get("firstSeen"), "lastSeen": row.get("lastSeen")}
    if was is not None:
        out["was"] = was
    if row.get("seeded"):
        out["seeded"] = True
    return out


def dropped(off, tags_doc):
    """DROP records for homes taken off as no longer for sale, where listing-tags.json still holds the address."""
    known = {}
    for t in (tags_doc or {}).get("listings") or []:
        if isinstance(t, dict):
            for u in [t.get("url")] + list(t.get("urls") or []):
                if u:
                    known.setdefault(u, t)
    out = []
    for url, day in off.items():
        t = known.get(url)
        if not t or not t.get("address"):
            continue
        out.append({"date": t.get("date"), "price": t.get("price") if type(t.get("price")) in (int, float) else None,
                    "address": t["address"], "type": None, "verdict": "drop", "tag": "off", "reason": "no longer for sale",
                    "publishers": [t["source"]] if t.get("source") else [], "links": [url] if url.startswith("http") else [],
                    "offAt": day})
    return out


def build(city, listings, state, tags_doc, now):
    rows = [r for r in listings.get("rows") or [] if isinstance(r, dict) and r.get("url")]
    read_at = _when(listings.get("asOf")) or now
    off = {u: d for u, d in (listings.get("offMarket") or {}).items() if isinstance(u, str) and isinstance(d, str)}
    records = [record_for(r, city, read_at) for r in rows] + dropped(off, tags_doc)
    records.sort(key=lambda r: (r.get("offAt") or r.get("firstSeen") or "", r.get("date") or "", r.get("address") or ""), reverse=True)
    records = records[:RECORDS]
    publishers = publisher_status(city, listings, state, now)
    skipped = {}
    for p in publishers:
        for k, v in (p.get("skipped") or {}).items():
            skipped[k] = skipped.get(k, 0) + v
    tags = {}
    for r in records:
        tags[r["tag"]] = tags.get(r["tag"], 0) + 1
    doc = {"city": city, "kind": "listings", "generatedAt": now.isoformat(timespec="seconds"), "readAt": listings.get("asOf"),
           "homes": len(rows), "seenThisWeek": listings.get("seenThisWeek"), "unseenDays": UNSEEN_DAYS,
           "publishers": publishers, "offMarket": {"count": len(off), "shown": tags.get("off", 0)},
           "counts": tags, "records": records}
    if skipped:
        doc["skipped"] = skipped
    return doc


def build_for(root, city, now, listings_root=None):
    """The document for one city, or None without a merged listings file."""
    base = Path(listings_root or root) / city
    listings = _read(base / "listings.json")
    if not isinstance(listings.get("rows"), list):
        return None
    return build(city, listings, _read(base / "harvest-state.json"), _read(Path(root) / city / "listing-tags.json"), now)
