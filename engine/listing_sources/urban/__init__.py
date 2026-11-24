"""Corcoran Urban Real Estate's Chicago for-sale search and published listing history."""
import re
from datetime import datetime
from html import unescape

from ..pages import facts, photo_urls, zip_of

NAME = "Corcoran Urban Real Estate"
HOST = "https://www.urbanrealestate.com"
PATHS = {"chicago": "/chicago/search?status=active&city=Chicago&ob=Newest&type=ATSF%2CDE%2CMF&v=g"}


def search_url(city, number):
    return HOST + PATHS[city]


def detail_links(page, city):
    links = re.findall(r'href="(https://www\.urbanrealestate\.com/property/[^"]+-Chicago-IL-[^"]+\.html)"', page.html, re.I)
    if not links:
        raise ValueError("Missing Chicago property links")
    return links


def property_row(page, url):
    if page.metadata.get("og:url") != url:
        raise ValueError("Unexpected Urban property page")
    fields = {k: unescape(re.sub("<[^>]+>", "", v)).strip() for k, v in
              re.findall(r'<dt class="col-xs-5">([^<]+)</dt>\s*<dd class="col-xs-7">(.*?)</dd>', page.html, re.S)}
    address = (page.metadata.get("og:title") or "").split(" - ")[0]
    price = re.search(r'class="bigprice">\$([\d,]+)</h3>', page.html)
    dates = re.findall(r'pd-datagroup-label">(\d{2}-\d{2}-\d{4})</span>\s*Listed at \$', page.html)
    if not (fields.get("Status", "").startswith("Active") and ", Chicago, IL " in address and price and dates
            and fields.get("Type") in {"Attached", "Detached", "Multi Unit", "Multi-Family"}):
        return None
    desc = re.search(r'Property Description</h2>\s*<div>(.*?)</div>', page.html, re.S)
    photos = re.findall(r'class="photo-slider-item"[^>]+data-src="([^"]+)"', page.html)
    return {"source": NAME, "url": url, "address": address, "price": int(price[1].replace(",", "")),
            "date": max(datetime.strptime(d, "%m-%d-%Y").date() for d in dates).isoformat(),
            "photos": photo_urls(photos, "https://img.urbanre.com/propertyimage/"),
            "description": unescape(re.sub("<[^>]+>", " ", desc[1])).strip() if desc else None,
            **facts(beds=fields.get("Beds"), sqft=fields.get("Sq. Ft."), kind=fields["Type"], zip_code=zip_of(address))}
