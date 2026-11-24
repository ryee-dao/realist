"""Weichert publishes the original listing event on each home's price history."""
import re
from datetime import datetime
from html import unescape

from ..pages import CITIES, facts, photo_urls

NAME = "Weichert"
HOST = "https://www.weichert.com"
PATHS = {"nyc": "/NY/Newyork/New_York/", "philadelphia": "/PA/Philadelphia/Philadelphia/", "chicago": "/Chicago/"}
SAMPLE = True  # one search page, sampled
MAX_PAGES = 1


def search_url(city, number):
    return HOST + PATHS[city] + "?type=new"


def detail_links(page, city):
    residences = [obj for obj in page.schema() if obj.get("@type") == "Residence"]
    if not residences:
        raise ValueError("Missing Weichert residences")
    return [HOST + obj["url"] for obj in residences if re.fullmatch(r"/\d+/", obj.get("url", ""))
            and obj.get("address", {}).get("addressRegion") == CITIES[city][1]]


def property_row(page, url):
    schema = list(page.schema())
    home = next((obj for obj in schema if obj.get("@type") == "Residence"), {})
    product = next((obj for obj in schema if obj.get("@type") == "Product"), {})
    if not home or not product:
        raise ValueError("Missing Weichert property markup")
    offer = product.get("offers", {})
    if not re.search(r"\bfor sale\b", home.get("description", ""), re.I):
        return None
    if offer.get("url") != url or offer.get("priceCurrency") != "USD" or offer.get("availability") != "inStock":
        return None
    # Explicit listing events, never a price change or the site's update date.
    dates = re.findall(r"<tr>\s*<td>([\d/]+)(?:(?!<tr).)*?<td>Listed Price\s*(?=<)", page.html, re.S | re.I)
    if not dates or not re.search(r'property-info-status[^>]*>\s*<span[^>]*></span>\s*Active\s*<', page.html, re.I):
        return None
    posted = max(datetime.strptime(d, "%m/%d/%Y").date() for d in dates)
    remarks = re.search(r'<div id=description\b[^>]*>(.*?)</div>', page.html, re.S)
    images = re.findall(r'<img\b[^>]*(?:\bsrc|\bdata-lazy)=["\']?(//d36xftgacqn2p\.cloudfront\.net/listingphotos[^\s"\'>]+)', page.html, re.I)
    return {"url": url, "source": NAME, "address": home["name"], "price": float(offer["price"]),
            "date": posted.isoformat(), "description": unescape(re.sub("<[^>]+>", " ", remarks[1])).strip() if remarks else None,
            "photos": photo_urls(["https:" + x for x in images] + [home.get("image")], "https://d36xftgacqn2p.cloudfront.net/listingphotos"),
            **home_facts(page.html, home)}


def home_facts(html, home):
    sizes = dict(re.findall(r'propertySizeDetails">\s*([^<]*?)\s*<span class="listingFeatureCountDisplay propertySizeDetailsLabels">'
                            r'\s*([^<]*?)\s*</span>', html))
    kind = re.search(r'listing-type-label>Property Type</div><div class=col-xs-6><span class=listing-feature-value-display>([^<]+)<', html)
    geo = {k: v for k, v in re.findall(r'<meta itemprop=(latitude|longitude) content="?([-\d.]+)', html)}
    return facts(sizes.get("Bedrooms"), sizes.get("Baths"), sizes.get("Sq"), unescape(kind[1]) if kind else None,
                 geo.get("latitude"), geo.get("longitude"), (home.get("address") or {}).get("postalCode"))
