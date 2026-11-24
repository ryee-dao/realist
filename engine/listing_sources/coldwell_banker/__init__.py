"""Coldwell Banker Realty publishes dated offers and galleries in Schema.org markup."""
import re
from html import unescape
from urllib.parse import urlsplit

from ..pages import facts, photo_urls, valid_price

NAME = "Coldwell Banker Realty"
HOST = "https://www.coldwellbankerhomes.com"
PATHS = {"nyc": "/ny/new-york/", "philadelphia": "/pa/philadelphia/", "chicago": "/il/chicago/"}


def search_url(city, number):
    base = HOST + PATHS[city]
    return base if number == 1 else f"{base}p_{number}/"


def detail_links(page, city):
    collection = next((s for s in page.schema() if s.get("@type") == "CollectionPage"), None)
    if collection is None:
        raise ValueError("Missing Coldwell Banker collection")
    badges = re.findall(r'class="property-status-indicator-text"[^>]*>([^<]+)', page.html)
    entries = collection["mainEntity"]["itemListElement"]
    if len(badges) == len(entries) and all(b == "Coming Soon" for b in badges):
        return []
    # NYC results use individual borough and postal city slugs.
    prefix = HOST + ("/ny/" if city == "nyc" else PATHS[city])
    return [s["url"] for s in entries
            if s.get("url", "").startswith(prefix) and "/pid_" in s["url"]]


def property_row(page, url):
    home = next((s for s in page.schema() if "RealEstateListing" in s.get("@type", [])), {})
    if not home:
        raise ValueError("Missing Coldwell Banker listing markup")
    offer = home.get("offers", {})
    if (home.get("url") != url or offer.get("availability") != "https://schema.org/InStock"
            or offer.get("priceCurrency") != "USD" or not valid_price(offer.get("price"))):
        return None
    if not re.search(r"<strong>Status:\s*</strong>\s*Active\s*</li>", page.html, re.I):
        return None
    day = home.get("datePosted", "")[:10]
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
        return None
    images = home.get("image") or []
    if isinstance(images, str):
        images = [images]
    return {"url": url, "source": NAME, "address": home["name"], "price": offer["price"], "date": day,
            "description": unescape(home.get("description") or "") or None,
            "photos": photo_urls((x for x in images if urlsplit(x).hostname in {"m.cbhomes.com", "m1.cbhomes.com"}), "https://"),
            **home_facts(home.get("mainEntity") or {})}


def home_facts(residence):
    features = {f.get("name"): f.get("value") for f in residence.get("amenityFeature") or [] if isinstance(f, dict)}
    geo, address = residence.get("geo") or {}, residence.get("address") or {}
    return facts(residence.get("numberOfBedrooms"), residence.get("numberOfBathroomsTotal"),
                 (residence.get("floorSize") or {}).get("value"), features.get("Property Type") or features.get("Structure Type"),
                 geo.get("latitude"), geo.get("longitude"), address.get("postalCode"))
