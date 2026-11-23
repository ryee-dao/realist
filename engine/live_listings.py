"""Read the published offers on Trulia's newest-listings pages, once per site build."""
import argparse
import http.client
import json
import math
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

HOST = "https://www.trulia.com"
CITIES = {
    "philadelphia": ("Philadelphia", "PA", "America/New_York"),
    "nyc": ("New_York", "NY", "America/New_York"),
    "chicago": ("Chicago", "IL", "America/Chicago"),
}
MAX_PAGES = 30


class ListingPage(HTMLParser):
    """Read only the published schema.org collection and links to other result pages."""
    def __init__(self):
        super().__init__()
        self.structured = False
        self.scripts = []
        self.links = set()

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "script":
            self.structured = attrs.get("type") == "application/ld+json"
        elif tag == "a":
            self.links.add(attrs.get("href"))

    def handle_endtag(self, tag):
        if tag == "script":
            self.structured = False

    def handle_data(self, data):
        if self.structured:
            self.scripts.append(data)

    def items(self):
        for script in self.scripts:
            block = json.loads(script)
            if isinstance(block, dict) and block.get("@type") == "CollectionPage":
                entity = block.get("mainEntity")
                if isinstance(entity, dict) and isinstance(entity.get("itemListElement"), list):
                    return entity["itemListElement"]
        raise ValueError("Missing listings collection in the search page")


def fetch_page(path):
    request = Request(HOST + path, headers={"User-Agent": "Realist/2.0 (public housing data research)"})
    with urlopen(request, timeout=30) as response:
        if response.url != request.full_url:
            raise ValueError("Search page redirected")
        return response.read().decode("utf-8")


def posted_day(value, zone):
    if not isinstance(value, str):
        return None
    try:
        if len(value) == 10:
            return date.fromisoformat(value)
        stamp = datetime.fromisoformat(value)
        return stamp.astimezone(zone).date() if stamp.tzinfo else None
    except ValueError:
        return None


def listing_rows(items, state, zone):
    for entry in items:
        item = entry.get("item", {}) if isinstance(entry, dict) else {}
        if not isinstance(item, dict) or item.get("@type") != "RealEstateListing":
            raise ValueError("Unexpected search result format")
        day = posted_day(item.get("datePosted"), zone)
        if day is None:
            continue
        for offer in item.get("offers") or []:
            if offer.get("availability") != "https://schema.org/InStock" or offer.get("priceCurrency") != "USD":
                continue
            address = (offer.get("itemOffered") or {}).get("address") or {}
            price, link = offer.get("price"), offer.get("url")
            if (type(price) not in (int, float) or not math.isfinite(price) or price <= 0
                    or not isinstance(link, str) or not link.startswith(HOST + "/home/")
                    or address.get("addressRegion") != state or not address.get("streetAddress")):
                continue
            label = ", ".join(filter(None, [address["streetAddress"], address.get("addressLocality")]))
            yield {"url": link.split("?")[0], "address": label, "price": price, "date": day.isoformat()}


def collect(city, now, fetch=fetch_page, pause=time.sleep):
    name, state, time_zone = CITIES[city]
    zone = ZoneInfo(time_zone)
    today = now.astimezone(zone).date()
    start = (today - timedelta(days=6)).isoformat()
    base = f"/for_sale/{name},{state}/new-listings/"
    path = base
    by_url = {}
    pages = 0
    partial = False
    for page_number in range(1, MAX_PAGES + 1):
        try:
            parsed = ListingPage()
            parsed.feed(fetch(path))
            parsed.close()
            items = parsed.items()
            rows = list(listing_rows(items, state, zone))
        except (ValueError, TypeError, AttributeError, OSError, http.client.HTTPException) as exc:
            if not pages:
                raise
            print(f"{city}: stopped at page {page_number}: {exc}", file=sys.stderr)
            partial = True
            break
        pages += 1
        for row in rows:
            if start <= row["date"] <= today.isoformat():
                by_url.setdefault(row["url"], row)
        path = f"{base}{page_number + 1}_p/"
        if path not in parsed.links:
            break
        if page_number == MAX_PAGES:
            partial = True
            break
        pause(1)
    return {"status": "ready", "source": "Trulia", "asOf": now.isoformat(), "pages": pages,
            "partial": partial, "rows": sorted(by_url.values(), key=lambda r: (r["date"], r["url"]), reverse=True)}


def publish(root, city):
    try:
        snapshot = collect(city, datetime.now(timezone.utc))
    except (ValueError, TypeError, AttributeError, OSError, http.client.HTTPException) as exc:
        print(f"Listing refresh failed for {city}: {exc}", file=sys.stderr)
        snapshot = {"status": "unavailable"}
    target = root / city / "new-listings.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("w", dir=target.parent, delete=False) as tmp:
        json.dump(snapshot, tmp, separators=(",", ":"), allow_nan=False)
    os.replace(tmp.name, target)
    print(f"{city}: {snapshot['status']}, {len(snapshot.get('rows', []))} listings", flush=True)
    return snapshot["status"] == "ready" and not snapshot["partial"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--city", choices=CITIES)
    parser.add_argument("--output", type=Path, default=Path("web/public/data"))
    args = parser.parse_args()
    results = [publish(args.output, city) for city in ([args.city] if args.city else CITIES)]
    sys.exit(0 if all(results) else 1)
