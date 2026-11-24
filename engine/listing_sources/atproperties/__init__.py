"""@properties Christie's International Real Estate publishes its Chicagoland just-listed search as page state."""
from datetime import datetime, timezone

from ..pages import InactiveListing, facts, photo_urls, script_value, valid_price

NAME = "@properties"
HOST = "https://www.atproperties.com"
PATHS = {"chicago": "/chicagoland/search/just-listed"}
SAMPLE = True  # one page of up to 200 Chicagoland listings, ordered by price rather than date
MAX_PAGES = 1
SEARCH_IMAGES = "https://d229qcohg01jma.cloudfront.net/MRED/"
GALLERY_IMAGES = "https://resources.atproperties.com/images/MRED/"
OPEN = {"active", "new", "open house", "price change", "back on market"}


def search_url(city, number):
    return HOST + PATHS[city]


def listed_on(value, zone):
    # listDate is UTC ("2026-11-21 06:00:00" is midnight in Chicago).
    try:
        return datetime.fromisoformat(value).replace(tzinfo=timezone.utc).astimezone(zone).date()
    except (TypeError, ValueError):
        return None


def home_facts(home):
    # "bathrooms" is written as full.half: "3.2" is three full and two half baths.
    full, _, half = str(home.get("bathrooms") or "").partition(".")
    baths = int(full) + 0.5 * int(half or 0) if full.isdigit() and (half or "0").isdigit() else None
    return facts(home.get("bedrooms"), baths, home.get("sqft"), home.get("type"),
                 home.get("lat"), home.get("lng"), home.get("zip"))


def search(page, city, zone):
    start = page.html.find("$app.load('listingSearch'")
    if start < 0:
        raise ValueError("Missing @properties listing search")
    homes = script_value(page.html, "searchResults", start)
    if not isinstance(homes, list):
        raise ValueError("Expected a list of homes")
    rows = []
    for home in homes:
        url = home.get("listingUrl") or ""
        day = listed_on(home.get("listDate"), zone)
        if (not home.get("isActive") or str(home.get("status", "")).lower() not in OPEN or home.get("isCommercial")
                or home.get("city") != "Chicago" or home.get("state") != "Illinois" or not day
                or not valid_price(home.get("rawPrice")) or not home.get("fullAddress") or not url.startswith(HOST + "/")):
            continue
        rows.append({"url": url, "address": home["fullAddress"], "date": day.isoformat(), "price": home["rawPrice"],
                     "source": NAME, "photos": photo_urls([home.get("imageUrl")], SEARCH_IMAGES), "summary": None,
                     "broker": (home.get("brokerReciprocity") or {}).get("officeName") or None, **home_facts(home)})
    return rows


def detail(page, url):
    start = page.html.find("$app.load('listingDetails'")
    if start < 0:
        raise ValueError("Missing @properties listing details")
    home = script_value(page.html, "listing", start)
    if home.get("listingUrl") != url:
        raise ValueError("Property no longer matches this listing")
    if not home.get("isActive") or str(home.get("status", "")).lower() not in OPEN:
        raise InactiveListing("Listing is no longer active for sale")
    images = (image.get("url") for image in home.get("images") or [] if isinstance(image, dict))
    return {"description": home.get("description"), "photos": photo_urls(images, GALLERY_IMAGES), **home_facts(home)}
