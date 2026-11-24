"""OneKey MLS, the New York multiple listing service, server-renders each area's latest residential sales."""
import json
import re

from ..pages import InactiveListing, facts, photo_urls, valid_price, zip_of

NAME = "OneKey MLS"
HOST = "https://www.onekeymls.com"
# Each area page renders 30 listings, most recently changed first; there is no crawlable second page.
AREAS = {"nyc": ["new-york-ny", "manhattan-ny", "brooklyn-ny", "bronx-ny", "staten-island-ny"]}
PATHS = AREAS
SAMPLE = True
LINKED_PAGES = False
IMAGE_PREFIX = "https://bpp.mlsgrid.com/images/"
# Postal prefixes of the five boroughs; 11004 and 11005 are the Queens parts of 110xx (the rest is Nassau).
NYC_ZIPS = re.compile(r"^(10[0-4]\d\d|11[1-4]\d\d|116\d\d|1100[45])$")


def search_url(city, number):
    areas = AREAS[city]
    return f"{HOST}/homes-for-sale/{areas[number - 1]}" if number <= len(areas) else None


def listings(page):
    data = page.flight()
    marker = re.search(r'"initialProperties"\s*:\s*', data)
    if not marker:
        raise ValueError("Missing OneKey listing results")
    homes = json.JSONDecoder().raw_decode(data, marker.end())[0]
    if not isinstance(homes, list):
        raise ValueError("Expected a list of homes")
    return homes


def search(page, city, zone):
    paths = {}
    for obj in page.schema():
        for item in ((obj.get("mainEntity") or {}).get("itemListElement") or []) if obj.get("@type") == "SearchResultsPage" else []:
            match = re.fullmatch(r"https://(?:www\.)?onekeymls\.com(/home-details/[a-z0-9-]+/(\w+))", item.get("url") or "")
            if match:
                paths[match[2]] = match[1]
    rows = []
    for home in listings(page):
        listing, computed = home.get("Listing") or {}, home.get("Computed") or {}
        address, structure = home.get("DisplayName") or "", home.get("Structure") or {}
        price = (listing.get("Price") or {}).get("ListPrice")
        # OnMarketDate is a calendar date written as UTC midnight.
        day = (computed.get("OnMarketDate") or "")[:10]
        zip_code = zip_of(address)
        if (listing.get("StandardStatus") != "Active" or listing.get("MlsStatus") != "Active"
                or "Sale" not in (computed.get("PropertySaleType") or []) or "Residential" not in (computed.get("PropertyCategory") or [])
                or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day) or not valid_price(price)
                or not zip_code or not NYC_ZIPS.match(zip_code) or home.get("BUPI") not in paths):
            continue
        point = home.get("LocationPoint") or {}
        rows.append({"url": HOST + paths[home["BUPI"]], "address": address, "date": day, "price": price, "source": NAME,
                     "photos": photo_urls(home.get("Images") or [], IMAGE_PREFIX), "summary": None,
                     "broker": ((listing.get("AgentOffice") or {}).get("ListOffice") or {}).get("ListOfficeName"),
                     **facts(structure.get("BedroomsTotal"), computed.get("BathroomsTotalInteger"), structure.get("LivingArea"),
                             home.get("PropertySubType"), point.get("lat"), point.get("lon"), zip_code)})
    return rows


def detail(page, url):
    listing = next((s for s in page.schema() if s.get("@type") == "RealEstateListing"), None)
    if listing is None:
        raise ValueError("Missing OneKey listing markup")
    if (listing.get("url") or "").rsplit("/", 1)[-1] != url.rsplit("/", 1)[-1]:
        raise ValueError("Property no longer matches this listing")
    offer = listing.get("offers") or {}
    if offer.get("availability") != "https://schema.org/InStock":
        raise InactiveListing("Listing is no longer active for sale")
    text = (offer.get("itemOffered") or {}).get("description") or ""
    # The markup wraps the remarks: "<street> - Residential in New York, NY. <remarks> View on OneKey® MLS."
    text = re.sub(r"\s*View on OneKey® MLS\.\s*$", "", re.sub(r"^[^\n]*? - [\w ]+ in [^.]+\. ", "", text, count=1))
    return {"description": text.strip() or None}
