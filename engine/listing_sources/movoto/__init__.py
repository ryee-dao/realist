"""Movoto's public, server-rendered city results and property pages."""
from ..pages import CITIES, InactiveListing, day_at, facts, photo_urls, valid_price

NAME = "Movoto"
HOST = "https://www.movoto.com"
SLUGS = {"nyc": "new-york-ny", "chicago": "chicago-il", "philadelphia": "philadelphia-pa"}
NYC_COUNTIES = {"Bronx County", "Kings County", "New York County", "Queens County", "Richmond County"}
IMAGE_PREFIX = "https://pi.movoto.com/p/"


def search_url(city, number):
    base = f"{HOST}/{SLUGS[city]}/"
    return base if number == 1 else f"{base}p-{number}/"


def active(home):
    return (home.get("status") == "Active" and not home.get("isRentals") and not home.get("isSold")
            and home.get("showListPrice") is not False and home.get("showOnMarketTime") is not False
            and home.get("propertyType") != "LAND")


def search(page, city, zone):
    homes = page.payload("__INITIAL_STATE__")["pageData"]["listings"]
    if not isinstance(homes, list):
        raise ValueError("Expected a list of homes")
    rows = []
    for home in homes:
        geo = home.get("geo") or {}
        path = home.get("path") or ""
        # Movoto publishes listDate as a local calendar date followed by 00:00:00.
        day = day_at((home.get("listDate") or "")[:10], zone)
        if (not active(home) or not day or not valid_price(home.get("listPrice"))
                or geo.get("state") != CITIES[city][1] or not geo.get("formatAddress")
                or (city == "nyc" and geo.get("county") not in NYC_COUNTIES)
                or (city != "nyc" and geo.get("city") != CITIES[city][0])
                or not path or path.startswith("/") or ":" in path):
            continue
        rows.append({"url": HOST + "/" + path, "address": geo["formatAddress"], "date": day.isoformat(),
                     "price": home["listPrice"], "source": NAME, "broker": home.get("officeListName"),
                     "photos": photo_urls([home.get("tnImgPath")], IMAGE_PREFIX), "summary": None,
                     **home_facts(home)})
    return rows


def home_facts(home):
    geo = home.get("geo") or {}
    # Movoto writes 0 baths when the MLS left the field empty.
    return facts(home.get("bed"), home.get("bath") or None, home.get("sqftTotal"), home.get("propertyTypeDisplayName"),
                 geo.get("lat"), geo.get("lng"), geo.get("zipcode") or home.get("zipCode"))


def detail(page, url):
    home = page.payload("__INITIAL_STATE__")["pageData"]
    if HOST + "/" + home["path"] != url:
        raise ValueError("Property no longer matches this listing")
    if not active(home):
        raise InactiveListing("Listing is no longer active for sale")
    images = (p.get("url") for group in home.get("categorizedPhotos") or [] for p in group.get("photos") or [])
    return {"description": home.get("description"), "photos": photo_urls(images, IMAGE_PREFIX), **home_facts(home)}
