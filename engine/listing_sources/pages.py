import json
import math
import re
from datetime import date, datetime
from html.parser import HTMLParser
from urllib.error import HTTPError
from urllib.request import Request, urlopen

CITIES = {
    "philadelphia": ("Philadelphia", "PA", "America/New_York"),
    "nyc": ("New_York", "NY", "America/New_York"),
    "chicago": ("Chicago", "IL", "America/Chicago"),
}
USER_AGENT = "Realist/2.0 (public housing data research)"
# Titles of interstitial bot-check pages. Such a page means the publisher is declining automated reads.
CHALLENGE_TITLES = re.compile(r"<title[^>]*>\s*(Just a moment|Attention Required|Access denied|Access to this page has been denied|Connection Verification"
                              r"|Pardon Our Interruption|Are you a robot)", re.I)


class InactiveListing(ValueError):
    """The property page explicitly reports that a search result is no longer active."""


class Blocked(OSError):
    """The publisher refused the request (HTTP 403/429 or a bot-check page). Back off; never work around it."""

    def __init__(self, message, code=None):
        super().__init__(message)
        self.code = code


class Page(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.html = html
        self.links = set()
        self.metadata = {}
        self.schema_scripts = []
        self.scripts = {}
        self.capture = None
        self.feed(html)
        self.close()

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "script":
            self.capture = None
            if attrs.get("type") == "application/ld+json":
                self.capture = "schema"
            elif attrs.get("type") == "application/json":
                self.capture = attrs.get("id")
        elif tag == "meta" and attrs.get("property"):
            self.metadata[attrs["property"]] = attrs.get("content")
        elif tag == "a":
            self.links.add(attrs.get("href"))

    def handle_endtag(self, tag):
        if tag == "script":
            self.capture = None

    def handle_data(self, data):
        if self.capture == "schema":
            self.schema_scripts.append(data)
        elif self.capture:
            self.scripts[self.capture] = data

    def schema(self):
        for raw in self.schema_scripts:
            obj = json.loads(raw)
            if isinstance(obj, list):
                yield from obj
            elif isinstance(obj, dict):
                yield from obj.get("@graph", [obj])

    def payload(self, name):
        if name not in self.scripts:
            raise ValueError(f"Missing published page data ({name})")
        return json.loads(self.scripts[name])

    def flight(self):
        """The text a Next.js app-router page streams in its self.__next_f.push([1, "..."]) scripts."""
        chunks = re.findall(r'self\.__next_f\.push\(\[1,("(?:[^"\\]|\\.)*")\]\)', self.html)
        return "".join(json.loads(chunk) for chunk in chunks)


def fetch(url):
    request = Request(url, headers={"User-Agent": USER_AGENT})
    try:
        response = urlopen(request, timeout=12)
    except HTTPError as exc:
        if exc.code in (403, 429):
            raise Blocked(f"HTTP {exc.code} from {url}", exc.code) from exc
        raise
    with response:
        if response.url != url:
            raise ValueError("Listing page redirected")
        body = response.read(8_000_001)
        if len(body) > 8_000_000:
            raise ValueError("Listing page exceeds size limit")
        if response.status == 202 and not body:
            raise Blocked(f"Empty HTTP 202 (bot check) from {url}", 202)
    html = body.decode("utf-8")
    if CHALLENGE_TITLES.search(html[:20_000]):
        raise Blocked(f"Bot-check page from {url}", response.status)
    return Page(html)


def script_value(text, key, start=0):
    """Decode the JSON value written on its own line as `key: <json>` in an inline page-state script, after `start`."""
    match = re.compile(rf"^[ \t]*{re.escape(key)}[ \t]*:[ \t]*", re.M).search(text, start)
    if not match:
        raise ValueError(f"Missing published page data ({key})")
    return json.JSONDecoder().raw_decode(text, match.end())[0]


def day_at(value, zone):
    if not isinstance(value, str):
        return None
    try:
        if len(value) == 10:
            return date.fromisoformat(value)
        stamp = datetime.fromisoformat(value)
        return stamp.astimezone(zone).date() if stamp.tzinfo else None
    except ValueError:
        return None


def valid_price(value):
    return type(value) in (int, float) and math.isfinite(value) and value > 0


def photo_urls(urls, prefix):
    return list(dict.fromkeys(url for url in urls if isinstance(url, str) and url.startswith(prefix)))


def number(value, minimum=0):
    """A published count or area ("1,667", "3.0", 4) as a number, or None when absent or below `minimum`."""
    if isinstance(value, str):
        value = value.replace(",", "").strip()
        try:
            value = float(value)
        except ValueError:
            return None
    if type(value) not in (int, float) or not math.isfinite(value) or value < minimum:
        return None
    return int(value) if value == int(value) else value


def facts(beds=None, baths=None, sqft=None, kind=None, lat=None, lon=None, zip_code=None):
    """Optional home facts in the shared row field names, leaving out whatever the page does not publish."""
    lat, lon = number(lat, -90), number(lon, -180)
    if lat is None or lon is None or not (-90 <= lat <= 90 and -180 <= lon <= 180) or (lat == 0 and lon == 0):
        lat = lon = None
    zip_code = zip_code.strip()[:5] if isinstance(zip_code, str) and re.match(r"\s*\d{5}", zip_code) else None
    values = {"beds": number(beds), "baths": number(baths, 0.5), "sqft": number(sqft, 1),
              "type": kind.strip() if isinstance(kind, str) and kind.strip() else None,
              "lat": lat, "lon": lon, "zip": zip_code}
    return {k: v for k, v in values.items() if v is not None}


def zip_of(address):
    match = re.search(r"\b(\d{5})(?:-\d{4})?\s*$", address or "")
    return match[1] if match else None
