import json
import math
from datetime import date, datetime
from html.parser import HTMLParser
from urllib.request import Request, urlopen

CITIES = {
    "philadelphia": ("Philadelphia", "PA", "America/New_York"),
    "nyc": ("New_York", "NY", "America/New_York"),
    "chicago": ("Chicago", "IL", "America/Chicago"),
}


class Page(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.links = set()
        self.scripts = {}
        self.capture = None
        self.feed(html)
        self.close()

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "script":
            self.capture = attrs.get("id") if attrs.get("type") == "application/json" else None
        elif tag == "a":
            self.links.add(attrs.get("href"))

    def handle_endtag(self, tag):
        if tag == "script":
            self.capture = None

    def handle_data(self, data):
        if self.capture:
            self.scripts[self.capture] = data

    def payload(self, name):
        if name not in self.scripts:
            raise ValueError(f"Missing published page data ({name})")
        return json.loads(self.scripts[name])


def fetch(url):
    request = Request(url, headers={"User-Agent": "Realist/2.0 (public housing data research)"})
    with urlopen(request, timeout=12) as response:
        if response.url != url:
            raise ValueError("Listing page redirected")
        body = response.read(8_000_001)
        if len(body) > 8_000_000:
            raise ValueError("Listing page exceeds size limit")
        return Page(body.decode("utf-8"))


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
