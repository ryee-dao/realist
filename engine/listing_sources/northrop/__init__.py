"""Northrop Realty's Philadelphia for-sale properties from its public search and map markup."""
import json
import re
from html import unescape

from ..pages import facts, number, photo_urls

NAME = "Northrop Realty"
HOST = "https://thegroup.northroprealty.com"
PATHS = {"philadelphia": "/?search_city=Philadelphia&sortorder=DESC-ListingDate"}
REQUEST_INTERVAL = 5


def search_url(city, number):
    return HOST + PATHS[city]


def detail_links(page, city):
    links = re.findall(r'href="(https://thegroup\.northroprealty\.com/listing/paph[^"]+-philadelphia-pa-\d{5}/)"', page.html)
    if not links:
        raise ValueError("Missing Philadelphia listings")
    return links


def property_row(page, url):
    match = re.search(r'<details-map\b[^>]*?:listing="([^"]+)"', page.html, re.S)
    if not match:
        raise ValueError("Missing Northrop listing data")
    d = json.loads(unescape(match[1]))
    if (d.get("url_details") != url or d.get("ListingStatus") != "Active" or d.get("ListingType") != "Residential"
            or d.get("AddressCity") != "Philadelphia" or d.get("AddressState") != "PA" or not d.get("Address")
            or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", d.get("ListingDate") or "")):
        return None
    return {"source": NAME, "url": url, "address": f"{d['Address']}, Philadelphia, PA {d.get('AddressZipCode') or ''}".strip(),
            "price": number(d["ListingPrice"], 1), "date": d["ListingDate"], "description": d.get("ListingRemarks"),
            "broker": d.get("ListingOffice"), "photos": photo_urls(d.get("thumbnails") or [d.get("ListingImage")],
                                                                   "https://feed-images.rewhosting.com/"),
            **facts(d.get("NumberOfBedrooms"), d.get("NumberOfBathrooms"), d.get("NumberOfSqFt"), d.get("ListingSubType"),
                    d.get("Latitude"), d.get("Longitude"), d.get("AddressZipCode"))}
