"""Trulia's public, server-rendered city results and property pages."""
from ..pages import CITIES, InactiveListing, day_at, photo_urls, valid_price

NAME = "Trulia"
HOST = "https://www.trulia.com"


def search_url(city, number):
    name, state, _ = CITIES[city]
    base = f"{HOST}/for_sale/{name},{state}/new-listings/"
    return base if number == 1 else f"{base}{number}_p/"


def photos(home):
    images = (home.get("media") or {}).get("photos") or []
    return photo_urls(((p.get("url") or {}).get("large") for p in images), HOST + "/pictures/")


def search(page, city, zone):
    homes = page.payload("__NEXT_DATA__")["props"]["searchData"]["homes"]
    if not isinstance(homes, list):
        raise ValueError("Expected a list of homes")
    rows = []
    for home in homes:
        location, price = home.get("location") or {}, home.get("price") or {}
        path = home.get("url") or ""
        listing = home.get("activeListing") or {}
        day = day_at(listing.get("dateListed"), zone)
        statuses = [tag.get("formattedName", "").upper() for tag in home.get("fullTags") or []]
        if any(s.startswith(("COMING SOON", "PENDING", "CONTINGENT")) for s in statuses):
            continue
        if (not (home.get("currentStatus") or {}).get("isActiveForSale") or not day
                or location.get("stateCode") != CITIES[city][1] or not location.get("streetAddress")
                or price.get("currencyCode") != "USD" or not valid_price(price.get("price"))
                or not path.startswith("/home/")):
            continue
        rows.append({"url": HOST + path, "address": location["fullLocation"], "date": day.isoformat(),
                     "price": price["price"], "source": NAME, "photos": photos(home),
                     "summary": (home.get("description") or {}).get("value"),
                     "broker": ((listing.get("provider") or {}).get("broker") or {}).get("name")})
    return rows


def detail(page, url):
    home = page.payload("__NEXT_DATA__")["props"]["homeDetails"]
    if HOST + home["url"] != url:
        raise ValueError("Property no longer matches this listing")
    if not home["currentStatus"]["isActiveForSale"] or home["currentStatus"].get("label") == "Coming Soon":
        raise InactiveListing("Listing is no longer active for sale")
    return {"description": (home.get("description") or {}).get("value"), "photos": photos(home)}
