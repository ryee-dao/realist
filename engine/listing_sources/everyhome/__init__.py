"""EveryHome's regional search cards include an explicit list date and brokerage status."""
import re
from datetime import datetime
from html import unescape

from ..pages import facts, photo_urls, zip_of

NAME = "EveryHome"
HOST = "https://www.everyhome.com"
PATHS = {"philadelphia": "/Home-Search/"}
SAMPLE = True  # one search page, sampled
MAX_PAGES = 1


def search_url(city, number):
    return HOST + PATHS[city]


def search(page, city, zone):
    cards = re.split(r"<div class=['\"]thumb col-lg-", page.html)[1:]
    if not cards:
        raise ValueError("Missing EveryHome listing cards")
    rows = []
    for card in cards:
        tour = re.search(r"data-stat='A'[^>]*data-address='([^']+)'", card)
        posted = re.search(r"Listed on (\w+ \d{1,2}, \d{4})", card)
        offer = re.search(r"href='(https://www\.everyhome\.com/Home/[^']+)'[^>]*>\$([\d,]+)</a>", card)
        if not (tour and posted and offer and re.search(r", Philadelphia, PA \d{5}$", tour[1])):
            continue
        images = re.findall(r"data-src='([^']+)'", card)
        broker = re.search(r"Listed by:\s*([^<]+)", card)
        rows.append({"url": offer[1], "source": NAME, "address": unescape(tour[1]),
                     "price": int(offer[2].replace(",", "")), "date": datetime.strptime(posted[1], "%b %d, %Y").date().isoformat(),
                     "photos": photo_urls(images, HOST + "/wee_pics/"), "broker": unescape(unescape(broker[1])).strip() if broker else None,  # published double-escaped
                     **facts(zip_code=zip_of(tour[1]))})
    return rows


def detail(page, url):
    if page.metadata.get("og:url") != url:
        raise ValueError("EveryHome property URL changed")
    description = re.search(r"<p class='descript'>(.*?)</p>", page.html, re.S)
    return {"description": unescape(re.sub("<[^>]+>", " ", description[1])).strip() if description else page.metadata.get("og:description"),
            "photos": photo_urls([page.metadata.get("og:image")], HOST + "/wee_pics/"),
            "galleryUrl": next((link for link in page.links if link and link == url.replace("/Home/", "/Photo-Gallery/")), None),
            **home_facts(page.html)}


def home_facts(html):
    details = {unescape(k).strip(): unescape(v).strip() for k, v in
               re.findall(r"<div class='detl col-xs-6'>([^<]*)</div><div class='detl col-xs-6'>([^<]*)</div>", html)}
    full, partial = (details.get(k, "").replace(",", "") for k in ("Full Baths", "Partial Baths"))
    baths = int(full) + 0.5 * int(partial or 0) if full.isdigit() and (partial or "0").isdigit() else None
    lat = re.search(r"Lattitude : (-?[\d.]+)<", html)
    lon = re.search(r"Longitude : (-?[\d.]+)<", html)
    return facts(details.get("Bedrooms"), baths, details.get("Interior SqFt"), None,
                 lat[1] if lat else None, lon[1] if lon else None)


def gallery(page):
    urls = re.findall(r"""<img[^>]+\bsrc=['"]([^'"]+)['"][^>]*\balt=['"]image\d+""", page.html, re.I)
    return photo_urls(urls, ("https://bright-media01.prd.brightmls.com/", HOST + "/"))
