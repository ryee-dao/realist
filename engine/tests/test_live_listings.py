import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from zoneinfo import ZoneInfo

from engine import live_listings as feed
from engine.listing_sources import atproperties, coldwell_banker, everyhome, movoto, onekey, pages, trulia, weichert
from engine.listing_sources.pages import Blocked, InactiveListing, Page


def state(script_id, payload):
    return Page(f'<script type="application/json" id="{script_id}">{json.dumps(payload)}</script>')


def schema(*objects):
    return ''.join(f'<script type="application/ld+json">{json.dumps(o)}</script>' for o in objects)


def flight(payload):
    """A Next.js app-router page streaming `payload` in two self.__next_f.push chunks, as OneKey does."""
    text = "8:" + json.dumps(payload)
    half = len(text) // 2
    return "".join(f"<script>self.__next_f.push([1,{json.dumps(part)}])</script>" for part in (text[:half], text[half:]))


class SourceTests(unittest.TestCase):
    now = datetime(2026, 11, 24, 2, tzinfo=timezone.utc)
    zone = ZoneInfo("America/Chicago")

    def test_trulia_date_and_gallery(self):
        img = trulia.HOST + "/pictures/photo.jpg"
        home = {"url": "/home/123", "location": {"streetAddress": "123 Main", "fullLocation": "123 Main, Chicago", "stateCode": "IL"},
                "price": {"currencyCode": "USD", "price": 200000}, "activeListing": {"dateListed": "2026-11-24T01:10:00Z"},
                "currentStatus": {"isActiveForSale": True}, "media": {"photos": [{"url": {"large": img}}]},
                "description": {"value": "An enclosed porch."}}
        page = state("__NEXT_DATA__", {"props": {"searchData": {"homes": [home]}, "homeDetails": home}})
        row, = trulia.search(page, "chicago", self.zone)
        self.assertEqual(row["date"], "2026-11-23")
        self.assertEqual(trulia.detail(page, row["url"]), {"description": "An enclosed porch.", "photos": [img]})

    def test_movoto_filters_facts_and_detail_identity(self):
        row = {"status": "Active", "path": "chicago-il/example/", "listDate": "2026-11-23 00:00:00", "listPrice": 300000,
               "bed": 3, "bath": 0, "sqftTotal": 1450, "propertyTypeDisplayName": "Townhouse",
               "geo": {"city": "Chicago", "state": "IL", "formatAddress": "123 Main, Chicago", "lat": 41.9, "lng": -87.7, "zipcode": "60614"}}
        page = state("__INITIAL_STATE__", {"pageData": {"listings": [row, dict(row, isRentals=True), dict(row, isSold=True)]}})
        result, = movoto.search(page, "chicago", self.zone)
        # A zero bath count is Movoto's placeholder for an empty MLS field.
        self.assertEqual({k: result[k] for k in ("beds", "sqft", "type", "lat", "lon", "zip")},
                         {"beds": 3, "sqft": 1450, "type": "Townhouse", "lat": 41.9, "lon": -87.7, "zip": "60614"})
        self.assertNotIn("baths", result)
        with self.assertRaises(ValueError):
            movoto.detail(state("__INITIAL_STATE__", {"pageData": row}), result["url"] + "other")

    def test_broker_detail_dates_and_facts(self):
        url = weichert.HOST + "/123/"
        offer = {"price": 200000, "url": url, "priceCurrency": "USD", "availability": "inStock"}
        data = schema({"@type": "Residence", "name": "123 Main", "description": "Condo for sale", "address": {"postalCode": "19115"}},
                      {"@type": "Product", "offers": offer})
        sizes = ''.join(f'<span class="pdp-listingLabel propertySizeDetails">{k}<span class="listingFeatureCountDisplay '
                        f'propertySizeDetailsLabels">{v}</span></span>' for k, v in (("Bedrooms", 4), ("Baths", "3.0"), (" Sq ", " 1,667 ")))
        html = (data + '<span class=property-info-status><span></span>Active</span><tr><td>11/20/2026<td>$200,000<td><td>Listed Price</table>'
                + sizes + '<meta itemprop=latitude content=40.0894><meta itemprop=longitude content=-75.0292>'
                + '<p class=listing-type-label>Property Type</div><div class=col-xs-6><span class=listing-feature-value-display> Townhouse </span>')
        self.assertIsNone(weichert.property_row(Page(html.replace("Condo for sale", "Boatslips for sale")), url))
        row = weichert.property_row(Page(html), url)
        self.assertEqual(row["date"], "2026-11-20")
        self.assertEqual({k: row[k] for k in feed.FACT_FIELDS},
                         {"beds": 4, "baths": 3, "sqft": 1667, "type": "Townhouse", "lat": 40.0894, "lon": -75.0292, "zip": "19115"})
        self.assertIsNone(weichert.property_row(Page(html.replace("Listed Price", "Price Change")), url))
        url = coldwell_banker.HOST + "/pa/philadelphia/property/pid_123/"
        residence = {"@type": "SingleFamilyResidence", "numberOfBedrooms": 2, "numberOfBathroomsTotal": 1, "floorSize": {"value": "840"},
                     "geo": {"latitude": 39.94, "longitude": -75.17}, "address": {"postalCode": "19146"},
                     "amenityFeature": [{"name": "Structure Type", "value": "Interior Row/Townhouse"}]}
        home = {"@type": ["Product", "RealEstateListing"], "name": "123 Main", "url": url, "datePosted": "2026-11-20",
                "offers": dict(offer, availability="https://schema.org/InStock"), "mainEntity": residence}
        self.assertIsNone(coldwell_banker.property_row(Page(schema(home)), url))
        row = coldwell_banker.property_row(Page(schema(home) + '<li><strong>Status: </strong> Active</li>'), url)
        self.assertEqual(row["date"], "2026-11-20")
        self.assertEqual((row["beds"], row["sqft"], row["type"], row["zip"]), (2, 840, "Interior Row/Townhouse", "19146"))

    def test_everyhome_only_eligible_city_and_published_photos(self):
        self.assertNotIn("everyhome", feed.eligible("nyc"))
        url = "https://bright-media01.prd.brightmls.com/example.jpg"
        self.assertEqual(everyhome.gallery(Page(f"<img src='{url}' alt='image1'/>")), [url])
        home = everyhome.HOST + "/Home/1-Main-Street-Philadelphia-Pennsylvania-19127"
        cells = "".join(f"<div class='detl col-xs-6'>{k}</div><div class='detl col-xs-6'>{v}</div>"
                        for k, v in (("Bedrooms", 4), ("Full Baths", 2), ("Partial Baths", 1), ("Interior SqFt", "2,520")))
        page = Page(f"<meta property='og:url' content='{home}'><p class='descript'>Porch &amp; yard</p>{cells}"
                    "<div class='x'>Lattitude : 40.03</div><div class='x'>Longitude : -75.22</div>")
        detail = everyhome.detail(page, home)
        self.assertEqual((detail["description"], detail["beds"], detail["baths"], detail["sqft"], detail["lat"]), ("Porch & yard", 4, 2.5, 2520, 40.03))

    def test_atproperties_search_and_detail(self):
        url = atproperties.HOST + "/12813435/220-s-green-street-4s-chicago-illinois-60607-nei"
        home = {"isActive": True, "status": "New", "listingUrl": url, "fullAddress": "220 S Green Street #4S, Chicago, IL 60607",
                "city": "Chicago", "state": "Illinois", "zip": "60607", "rawPrice": 2249000, "listDate": "2026-11-21 06:00:00",
                "imageUrl": "https://d229qcohg01jma.cloudfront.net/MRED/12/813/435/1.jpg", "bedrooms": 4, "bathrooms": "3.2",
                "sqft": "3,878", "type": "Condo", "lat": 41.878, "lng": -87.648, "brokerReciprocity": {"officeName": None}}
        others = [dict(home, status="Contingent"), dict(home, status="Coming Soon", isActive=False), dict(home, city="Winnetka"),
                  dict(home, listDate="2026-11-21 05:59:00")]
        html = ("<script>\n    $app.load('listingSearch', {\n        searchId: 339626,\n        searchResults: "
                + json.dumps([home] + others) + ",\n        sortBy: {}\n    });\n</script>")
        rows = atproperties.search(Page(html), "chicago", self.zone)
        self.assertEqual([r["date"] for r in rows], ["2026-11-21", "2026-11-20"])  # listDate is UTC
        self.assertEqual((rows[0]["baths"], rows[0]["sqft"], rows[0]["zip"]), (4, 3878, "60607"))
        gallery = [{"url": f"https://resources.atproperties.com/images/MRED/12/813/435/x/{n}.jpg"} for n in (1, 2)]
        detail_page = Page("<script>\n    $app.load('listingDetails', {\n        agent: {\"bio\": \"Her listing: 40 homes\"},\n"
                           "        listing: " + json.dumps(dict(home, description="A penthouse.", images=gallery)) + ",\n    });\n</script>")
        detail = atproperties.detail(detail_page, url)
        self.assertEqual((detail["description"], len(detail["photos"])), ("A penthouse.", 2))
        with self.assertRaises(InactiveListing):
            atproperties.detail(Page(detail_page.html.replace('"status": "New"', '"status": "Pending"')), url)

    def test_onekey_search_and_detail(self):
        path = "/home-details/7-park-ave-unit-142-new-york-manhattan-ny-10016/cjAsMJl3fKK"
        home = {"BUPI": "cjAsMJl3fKK", "DisplayName": "7 Park Ave Unit 142, New York (manhattan), NY 10016",
                "Computed": {"OnMarketDate": "2026-11-23T00:00:00.000Z", "PropertySaleType": ["Sale"], "PropertyCategory": ["Residential"],
                             "BathroomsTotalInteger": 1},
                "Images": ["https://bpp.mlsgrid.com/images/KEY1/a.jpeg", "https://bpp.mlsgrid.com/images/KEY1/b.jpeg"],
                "Listing": {"StandardStatus": "Active", "MlsStatus": "Active", "Price": {"ListPrice": 375000},
                            "AgentOffice": {"ListOffice": {"ListOfficeName": "ERealty Advisors Inc"}}},
                "LocationPoint": {"lat": 40.747, "lon": -73.981}, "PropertySubType": "Stock Cooperative",
                "Structure": {"BedroomsTotal": 0, "LivingArea": 402}}
        pending = dict(home, BUPI="p2", Listing=dict(home["Listing"], StandardStatus="Pending"))
        nassau = dict(home, BUPI="n3", DisplayName="1 Main St, Elmont, NY 11003")
        results = {"@type": "SearchResultsPage", "mainEntity": {"itemListElement": [
            {"url": "https://onekeymls.com" + path}, {"url": "https://onekeymls.com/home-details/x/p2"}, {"url": "https://onekeymls.com/home-details/y/n3"}]}}
        page = Page(schema({"@graph": [results]}) + flight({"initialProperties": [home, pending, nassau], "initialTotalCount": 3}))
        row, = onekey.search(page, "nyc", ZoneInfo("America/New_York"))
        self.assertEqual((row["url"], row["date"], row["beds"], row["sqft"], row["zip"], len(row["photos"])),
                         (onekey.HOST + path, "2026-11-23", 0, 402, "10016", 2))
        self.assertEqual(onekey.search_url("nyc", 1), onekey.HOST + "/homes-for-sale/new-york-ny")
        self.assertIsNone(onekey.search_url("nyc", len(onekey.AREAS["nyc"]) + 1))
        offered = {"description": "7 Park Ave - Residential in New York, NY. A prewar studio. View on OneKey® MLS."}
        listing = {"@type": "RealEstateListing", "url": "https://onekeymls.com" + path,
                   "offers": {"availability": "https://schema.org/InStock", "itemOffered": offered}}
        self.assertEqual(onekey.detail(Page(schema(listing)), row["url"]), {"description": "A prewar studio."})
        listing["offers"]["availability"] = "https://schema.org/SoldOut"
        with self.assertRaises(InactiveListing):
            onekey.detail(Page(schema(listing)), row["url"])


class FetchTests(unittest.TestCase):
    class Response(BytesIO):
        def __init__(self, body, url, status=200):
            super().__init__(body)
            self.url, self.status = url, status

    def test_refusals_are_reported_as_blocked(self):
        url = "https://example.com/homes"
        refusals = [HTTPError(url, 403, "Forbidden", {}, None), HTTPError(url, 429, "Too Many Requests", {}, None),
                    self.Response(b"", url, 202), self.Response(b"<html><title>Just a moment...</title></html>", url)]
        for refusal in refusals:
            effect = {"side_effect": refusal} if isinstance(refusal, Exception) else {"return_value": refusal}
            with patch.object(pages, "urlopen", **effect), self.assertRaises(Blocked):
                pages.fetch(url)
        with patch.object(pages, "urlopen", side_effect=HTTPError(url, 404, "Not Found", {}, None)), self.assertRaises(HTTPError) as caught:
            pages.fetch(url)
        self.assertNotIsInstance(caught.exception, Blocked)
        with patch.object(pages, "urlopen", return_value=self.Response(b"<a href='/p-2/'>next</a>", url)):
            self.assertIn("/p-2/", pages.fetch(url).links)


def home(url, date="2026-11-23", price=300000, address="123 Main St, Chicago, IL 60614", **extra):
    return {"url": url, "address": address, "date": date, "price": price, "source": url.split("/")[2], "photos": [], **extra}


class ReadSourceTests(unittest.TestCase):
    now = datetime(2026, 11, 24, 15, tzinfo=timezone.utc)

    def test_single_page_search_is_read_once(self):
        card = ("<div class='thumb col-lg-3'><a data-stat='A' data-address='1 Main Street, Philadelphia, PA 19127'></a>"
                "Listed on Nov 23, 2026<a href='https://www.everyhome.com/Home/1-Main' class='p'>$300,000</a></div>")
        html = f"<a href='{everyhome.search_url('philadelphia', 1)}'>search</a>" + card
        calls = []
        result = feed.read_source(everyhome, "philadelphia", self.now, lambda url: calls.append(url) or Page(html), lambda _: None)
        self.assertEqual((len(calls), result["pages"], result["partial"], len(result["rows"])), (1, 1, True, 1))
        self.assertEqual(result["rows"][0]["zip"], "19127")


class RotationTests(unittest.TestCase):
    now = datetime(2026, 11, 24, 15, tzinfo=timezone.utc)

    def test_every_publisher_gets_a_turn_before_any_repeats(self):
        publishers, order = {}, []
        names = feed.eligible("chicago")
        for run in range(2 * len(names)):
            when = self.now + timedelta(hours=6 * run)
            with patch.object(feed, "read_source", side_effect=lambda source, *a: {
                    "source": source.NAME, "pages": 1, "partial": False, "interrupted": False, "error": None, "rows": [home("https://x/1")]}):
                order.append(feed.collect("chicago", when, publishers, {})["provider"])
        self.assertEqual(order, names + names)
        self.assertNotIn("trulia", names)
        self.assertEqual(publishers["movoto"]["failures"], 0)

    def test_failover_records_failure_and_staleness(self):
        publishers = {}
        source = {"rows": [home("https://x/1")], "source": "Weichert", "pages": 1, "partial": False, "interrupted": False, "error": None}
        with patch.object(feed, "read_source", side_effect=[ValueError("no search page"), source]):
            result = feed.collect("philadelphia", self.now, publishers, {})
        self.assertEqual(result["provider"], "weichert")
        self.assertEqual((publishers["movoto"]["failures"], publishers["movoto"]["lastError"]), (1, "no search page"))
        self.assertEqual(publishers["weichert"]["lastSuccessAt"], self.now.isoformat())
        # Movoto failed and Weichert was read, so the next run starts with the publishers not yet tried.
        self.assertEqual(feed.rotation("philadelphia", publishers, self.now)[:2], ["coldwell_banker", "everyhome"])
        with patch.object(feed, "read_source", side_effect=OSError("offline")):
            stale = feed.collect("philadelphia", self.now, publishers, result)
        self.assertTrue(stale["stale"])
        self.assertEqual((stale["asOf"], stale["rows"]), (result["asOf"], result["rows"]))

    def test_refusals_cool_down_for_one_three_then_seven_days(self):
        publishers = {}
        for days in (1, 3, 7, 7):
            with patch.object(feed, "read_source", side_effect=Blocked("HTTP 403", 403)):
                feed.collect("nyc", self.now, publishers, {}, requested="onekey")
            until = datetime.fromisoformat(publishers["onekey"]["coolUntil"])
            self.assertEqual(until - self.now, timedelta(days=days))
        self.assertNotIn("onekey", feed.rotation("nyc", publishers, self.now + timedelta(days=6)))
        self.assertIn("onekey", feed.rotation("nyc", publishers, self.now + timedelta(days=7)))
        note, = [n for n in feed.attention("nyc", publishers, self.now) if n["provider"] == "onekey"]
        self.assertEqual((note["failures"], note["lastError"]), (4, "HTTP 403"))
        feed.note_success(publishers["onekey"], self.now, 5)
        self.assertEqual((publishers["onekey"]["blocks"], publishers["onekey"]["coolUntil"]), (0, None))

    def test_state_persists_between_runs(self):
        pages_by_url = {movoto.search_url("chicago", 1): "movoto", weichert.search_url("chicago", 1): "weichert"}

        def load(url):
            if pages_by_url.get(url) == "movoto":
                raise Blocked("HTTP 429", 429)
            raise ValueError("no fixture")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertFalse(feed.publish(root, "chicago", None, 0, now=self.now, load=load, pause=lambda _: None))
            saved = json.loads((root / "chicago" / "harvest-state.json").read_text())["publishers"]
            self.assertEqual(set(saved), set(feed.eligible("chicago")))  # every publisher failed, so each was tried
            self.assertTrue(saved["movoto"]["coolUntil"] > self.now.isoformat())
            later = self.now + timedelta(hours=6)
            with patch.object(feed, "read_source", side_effect=ValueError("still down")) as read:
                feed.publish(root, "chicago", None, 0, now=later, load=load, pause=lambda _: None)
            self.assertNotIn(movoto, [call.args[0] for call in read.call_args_list])
            snapshot = json.loads((root / "chicago" / "new-listings.json").read_text())
            self.assertEqual(snapshot["status"], "unavailable")
            self.assertIn("movoto", [n["provider"] for n in snapshot["attention"]])


class EnrichTests(unittest.TestCase):
    now = datetime(2026, 11, 24, 2, tzinfo=timezone.utc)

    def test_details_are_reused_and_new_listings_are_prioritized(self):
        cached = {"url": "existing", "date": "2026-11-23", "price": 200000, "description": "Porch", "beds": 0,
                  "photos": ["photo"], "detailAt": self.now.isoformat()}
        snapshot = {"provider": "movoto", "rows": [{k: cached[k] for k in ("url", "date", "price")}]}
        feed.enrich(snapshot, {"rows": [cached]}, self.now, 1, load=lambda _: self.fail("unexpected request"))
        self.assertEqual((snapshot["rows"][0]["description"], snapshot["rows"][0]["beds"]), ("Porch", 0))

    def test_refusal_stops_details(self):
        snapshot = {"provider": "movoto", "rows": [{"url": f"u{n}", "date": "2026-11-23", "price": 1} for n in range(3)]}
        calls = []

        def load(url):
            calls.append(url)
            raise Blocked("HTTP 403", 403)

        self.assertIsInstance(feed.enrich(snapshot, {}, self.now, 3, load=load, pause=lambda _: None), Blocked)
        self.assertEqual(len(calls), 1)


class MergeTests(unittest.TestCase):
    now = datetime(2026, 11, 24, 15, tzinfo=timezone.utc)

    def test_address_keys(self):
        same = [("220 S Green Street #4S, Chicago, IL 60607", "220 South Green St Unit 4S Chicago, IL 60607"),
                ("144 E 84th St Apt 4E New York, NY 10028", "144 East 84th Street #4E, New York, NY 10028"),
                ("84-27 85th Dr, Woodhaven, NY 11421", "8427 85th Drive Woodhaven, NY 11421"),
                ("29 E Meehan Ave Philadelphia, PA 19119", "29 East Meehan Avenue, Philadelphia, PA, 19119"),
                ("7 Park Ave Unit 142, New York (manhattan), NY 10016", "7 PARK AVENUE #142, NEW YORK, NY 10016")]
        for a, b in same:
            self.assertEqual(feed.home_key(a), feed.home_key(b), (a, b))
        self.assertEqual(feed.home_key("2429-31 N Front St 31 Philadelphia, PA 19133"), "2429 N FRONT ST #31 19133")
        self.assertEqual(feed.home_key("1600 Broadway Unit 5, New York, NY 10019"), "1600 BROADWAY #5 10019")
        self.assertNotEqual(feed.home_key("220 S Green St #4S, Chicago, IL 60607"), feed.home_key("220 S Green St #5S, Chicago, IL 60607"))
        self.assertNotEqual(feed.home_key("12 Elm St, Chicago, IL 60614"), feed.home_key("12 Elm St, Chicago, IL 60640"))
        self.assertIsNone(feed.home_key("Lot 5 Main St"))

    def test_merge_reads_every_publishers_latest_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            def snapshot(provider, as_of, rows):
                feed.write_snapshot(root / "sources" / provider / "chicago" / "new-listings.json",
                                    {"status": "ready", "source": provider, "asOf": as_of.isoformat(), "partial": False, "rows": rows})

            snapshot("movoto", self.now - timedelta(days=2), [
                home("https://movoto/1", "2026-11-20", 310000, "220 S Green St Unit 4S Chicago, IL 60607", photos=["m1"]),
                home("https://movoto/2", "2026-11-10", address="9 Old Rd, Chicago, IL 60601")])  # older, still kept
            snapshot("atproperties", self.now, [
                home("https://at/1", "2026-11-21", 299000, "220 S Green Street #4S, Chicago, IL 60607", photos=["a1", "a2"],
                     description="A penthouse.", beds=4),
                home("https://at/2", "2026-11-22", address="5 Elm St, Chicago, IL 60614"),
                home("https://at/3", "2026-11-22", address="5 Elm St, Chicago, IL 60614")])  # same publisher, another unit
            snapshot("weichert", self.now - timedelta(days=8), [home("https://w/1", "2026-11-23", address="1 Oak St, Chicago, IL 60601")])
            merged = feed.merge_city(root, "chicago", self.now)
        self.assertEqual(set(merged["publishers"]), {"movoto", "atproperties"})  # Weichert's snapshot is too old
        self.assertEqual((merged["entries"], merged["homes"]), (5, 4))
        green, = [r for r in merged["rows"] if r["key"] == "220 S GREEN ST #4S 60607"]
        self.assertEqual((green["date"], green["price"], green["url"]), ("2026-11-20", 299000, "https://at/1"))
        self.assertEqual((green["photos"], green["description"], green["beds"]), (["a1", "a2"], "A penthouse.", 4))
        self.assertEqual([e["provider"] for e in green["sources"]], ["atproperties", "movoto"])
        self.assertEqual({e["url"] for e in green["sources"]}, {"https://at/1", "https://movoto/1"})

    def test_homes_accumulate_until_too_old_or_off_the_market(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "sources" / "movoto" / "chicago" / "new-listings.json"

            def run(now, rows, inactive=()):
                feed.write_snapshot(path, {"status": "ready", "source": "Movoto", "asOf": now.isoformat(), "rows": rows})
                merged = feed.merge_city(root, "chicago", now, inactive)
                feed.write_snapshot(root / "chicago" / "listings.json", merged)
                return merged, {r["url"]: r for r in merged["rows"]}

            first = self.now - timedelta(days=40)
            run(first, [home("https://m/a", "2026-10-14", address="1 A St, Chicago, IL 60601", photos=["p1"], description="Nice."),
                        home("https://m/b", "2026-10-14", address="2 B St, Chicago, IL 60601"),
                        home("https://m/c", "2026-10-13", address="3 C St, Chicago, IL 60601")])
            merged, rows = run(self.now - timedelta(days=20), [home("https://m/b", "2026-10-14", address="2 B St, Chicago, IL 60601")])
            self.assertEqual(set(rows), {"https://m/a", "https://m/b", "https://m/c"})   # not seen again is not dropped
            self.assertEqual(rows["https://m/a"]["lastSeen"], first.isoformat())
            self.assertEqual(rows["https://m/b"]["firstSeen"], first.isoformat())
            self.assertEqual(rows["https://m/b"]["lastSeen"], (self.now - timedelta(days=20)).isoformat())
            # now: a property page says C is off the market; A is over 30 days old, so it is slimmed
            merged, rows = run(self.now, [home("https://m/d", "2026-11-23", address="4 D St, Chicago, IL 60601", photos=["p2"])],
                               inactive=["https://m/c"])
            self.assertEqual(set(rows), {"https://m/a", "https://m/b", "https://m/d"})
            self.assertNotIn("photos", rows["https://m/a"])
            self.assertTrue(all("photos" not in e and "description" not in e for e in rows["https://m/a"]["sources"]))
            self.assertEqual(rows["https://m/a"]["description"], "Nice.")
            self.assertEqual(rows["https://m/d"]["photos"], ["p2"])
            self.assertEqual(merged["offMarket"], {"https://m/c": feed.window("chicago", self.now)[1]})
            self.assertEqual(merged["seenThisWeek"], 1)
            # 181 days after A and B were listed they are dropped; D stays
            later = datetime(2027, 4, 14, 2, tzinfo=timezone.utc)
            _, rows = run(later, [])
            self.assertEqual(set(rows), {"https://m/d"})


class SeedTests(unittest.TestCase):
    now = datetime(2026, 11, 24, 15, tzinfo=timezone.utc)

    def local_file(self, root):
        rows = [
            {"key": "1 A ST 60601", "url": "https://m/a", "address": "1 A St, Chicago, IL 60601", "date": "2026-11-20", "price": 300000,
             "source": "Movoto", "photos": ["https://img/1.jpg"], "description": "Lovely home.", "summary": "Lovely", "broker": "X Realty",
             "beds": 3, "sqft": 1200, "lat": 41.9, "lon": -87.6, "zip": "60601",
             "firstSeen": "2026-11-20T01:00:00+00:00", "lastSeen": "2026-11-22T01:00:00+00:00",
             "sources": [{"provider": "movoto", "source": "Movoto", "url": "https://m/a", "asOf": "2026-11-22T01:00:00+00:00",
                          "photos": ["https://img/1.jpg"], "description": "Lovely home.", "price": 300000}]},
            {"key": "2 B ST 60601", "url": "https://m/b", "address": "2 B St, Chicago, IL 60601", "date": "2026-11-21", "price": 250000,
             "source": "Movoto", "sources": [{"provider": "movoto", "source": "Movoto", "url": "https://m/b", "asOf": "2026-11-21T01:00:00+00:00"}]},
            {"key": "3 C ST 60601", "url": "https://m/c", "address": "3 C St, Chicago, IL 60601", "date": "2026-04-01", "price": 200000,
             "source": "Movoto", "sources": []},   # listed over 180 days ago
        ]
        feed.write_snapshot(root / "chicago" / "listings.json", {"status": "ready", "rows": rows})

    def test_export_keeps_only_published_facts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, out = Path(tmp, "data"), Path(tmp, "seed")
            self.local_file(root)
            self.assertEqual(feed.export_seed(root, out, self.now, ["chicago"]), {"chicago": 2})
            seed = json.loads((out / "chicago.json").read_text())
        text = json.dumps(seed)
        for banned in ("description", "photos", "summary", "broker", "Lovely", "img/1.jpg", "X Realty"):
            self.assertNotIn(banned, text)
        a = seed["rows"][0]
        self.assertEqual((a["key"], a["price"], a["beds"], a["lat"], a["lastSeen"]), ("1 A ST 60601", 300000, 3, 41.9, "2026-11-22T01:00:00+00:00"))
        self.assertEqual(a["sources"], [{"provider": "movoto", "source": "Movoto", "url": "https://m/a", "asOf": "2026-11-22T01:00:00+00:00"}])
        self.assertEqual(seed["rows"][1]["lastSeen"], "2026-11-21T01:00:00+00:00")   # from the read time when not recorded

    def test_import_adds_never_overwrites_is_idempotent_and_yields_to_a_real_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            local, seed, hosted = Path(tmp, "local"), Path(tmp, "seed"), Path(tmp, "hosted")
            self.local_file(local)
            feed.export_seed(local, seed, self.now, ["chicago"])
            # the hosted store already holds B, read by its harvester at a different price
            feed.write_snapshot(hosted / "chicago" / "listings.json", {"status": "ready", "rows": [
                {"key": "2 B ST 60601", "url": "https://w/b", "address": "2 B Street, Chicago, IL 60601", "date": "2026-11-21",
                 "price": 249000, "source": "Weichert", "sources": [{"provider": "weichert", "url": "https://w/b", "asOf": "2026-11-23T01:00:00+00:00"}]}]})
            self.assertEqual(feed.import_seed(seed, hosted, self.now, ["chicago"]), {"chicago": 1})
            self.assertEqual(feed.import_seed(seed, hosted, self.now, ["chicago"]), {"chicago": 0})
            merged = json.loads((hosted / "chicago" / "listings.json").read_text())
            by = {r["key"]: r for r in merged["rows"]}
            self.assertEqual(by["2 B ST 60601"]["price"], 249000)
            self.assertNotIn("seeded", by["2 B ST 60601"])
            self.assertTrue(by["1 A ST 60601"]["seeded"])
            self.assertEqual(by["1 A ST 60601"]["lastSeen"], "2026-11-22T01:00:00+00:00")
            # an ordinary merge keeps the stub; a later read of A replaces it with the full record
            feed.write_snapshot(hosted / "sources" / "movoto" / "chicago" / "new-listings.json", {"status": "ready", "source": "Movoto",
                "asOf": self.now.isoformat(), "rows": [home("https://m/a", "2026-11-20", 295000, "1 A Street, Chicago, IL 60601", photos=["p"])]})
            merged = feed.merge_city(hosted, "chicago", self.now)
        a, = [r for r in merged["rows"] if r["key"] == "1 A ST 60601"]
        self.assertNotIn("seeded", a)
        self.assertEqual((a["price"], a["photos"], a["firstSeen"]), (295000, ["p"], "2026-11-20T01:00:00+00:00"))
        self.assertEqual(a["lastSeen"], self.now.isoformat())


if __name__ == "__main__":
    unittest.main()
