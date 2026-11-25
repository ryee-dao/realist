import json
import re
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from engine import listing_cards as lc
from engine import listings_intake as li
from engine import live_listings as feed

NOW = datetime(2026, 11, 24, 12, tzinfo=timezone.utc)
PHOTO = "https://img.example.com/photo/1.jpg"


def write(root, path, obj):
    p = Path(root, path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj))


def source(provider, name, url, as_of, price, **extra):
    return {"provider": provider, "source": name, "url": url, "asOf": as_of, "price": price, "address": "x",
            "photos": [PHOTO], "description": "Sunny rowhome, call Jane Broker", "broker": "Jane Broker Realty", **extra}


def home(url, address, date, price, first, last, sources, **extra):
    return {"key": address.upper(), "url": url, "address": address, "date": date, "price": price, "source": sources[0]["source"],
            "photos": [PHOTO], "description": "Sunny rowhome", "summary": "Rowhome", "broker": "Jane Broker Realty",
            "firstSeen": first, "lastSeen": last, "sources": sources, "type": "Row/Townhouse", "zip": "19125", **extra}


LISTINGS = {
    "status": "ready", "city": "philadelphia", "asOf": "2026-11-24T10:00:00+00:00", "homes": 4, "seenThisWeek": 3,
    "publishers": {
        "movoto": {"source": "Movoto", "asOf": "2026-11-24T10:00:00+00:00", "partial": True, "rows": 3,
                   "skipped": {"date": 12, "price": 1}},
        "weichert": {"source": "Weichert", "asOf": "2026-11-23T10:00:00+00:00", "partial": False, "rows": 2},
    },
    "offMarket": {"https://www.movoto.com/gone-1/": "2026-11-23", "https://www.movoto.com/gone-2/": "2026-11-22"},
    "rows": [
        home("https://www.movoto.com/a/", "1 Alpha St", "2026-11-23", 300000, "2026-11-24T10:00:00+00:00",
             "2026-11-24T10:00:00+00:00", [source("movoto", "Movoto", "https://www.movoto.com/a/", "2026-11-24T10:00:00+00:00", 300000)]),
        home("https://www.movoto.com/b/", "2 Beta St", "2026-11-20", 410000, "2026-11-22T10:00:00+00:00",
             "2026-11-24T10:00:00+00:00", [source("movoto", "Movoto", "https://www.movoto.com/b/", "2026-11-24T10:00:00+00:00", 410000),
                                          source("weichert", "Weichert", "https://www.weichert.com/b/", "2026-11-23T10:00:00+00:00", 410000)]),
        home("https://www.movoto.com/c/", "3 Gamma St", "2026-11-18", 250000, "2026-11-20T10:00:00+00:00",
             "2026-11-24T10:00:00+00:00", [source("movoto", "Movoto", "https://www.movoto.com/c/", "2026-11-24T10:00:00+00:00", 250000),
                                          source("weichert", "Weichert", "https://www.weichert.com/c/", "2026-11-23T10:00:00+00:00", 265000)]),
        home("https://www.weichert.com/d/", "4 Delta St", "2026-10-01", 199000, "2026-10-02T10:00:00+00:00",
             "2026-11-01T10:00:00+00:00", [source("weichert", "Weichert", "https://www.weichert.com/d/", "2026-11-01T10:00:00+00:00", 199000)]),
    ],
}
STATE = {"publishers": {
    "movoto": {"lastAttemptAt": "2026-11-24T10:00:00+00:00", "lastSuccessAt": "2026-11-24T10:00:00+00:00", "failures": 0},
    "weichert": {"lastAttemptAt": "2026-11-23T10:00:00+00:00", "lastSuccessAt": "2026-11-23T10:00:00+00:00", "failures": 0},
    "everyhome": {"lastAttemptAt": "2026-11-24T09:00:00+00:00", "lastSuccessAt": "2026-11-10T09:00:00+00:00", "failures": 2,
                  "blocks": 1, "coolUntil": "2026-11-25T09:00:00+00:00",
                  "lastError": "HTTP 403 from https://www.everyhome.com/Home-Search/?page=1"},
    "coldwell_banker": {"lastAttemptAt": "2026-11-24T08:00:00+00:00", "lastSuccessAt": "2026-11-20T08:00:00+00:00",
                        "failures": 1, "coolUntil": None, "lastError": "no listings dated in the last 7 days"},
}}
TAGS = {"listings": [{"url": "https://www.movoto.com/gone-1/", "address": "9 Omega St", "date": "2026-11-15", "price": 150000,
                      "source": "Movoto", "photos": [PHOTO], "description": "text"}]}


class ListingsIntakeTest(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict("os.environ", {"REALIST_LISTING_TRANSPORT": "http"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.doc = li.build("philadelphia", json.loads(json.dumps(LISTINGS)), STATE, TAGS, NOW)
        self.by_address = {r["address"]: r for r in self.doc["records"]}

    def test_shape_and_ordering(self):
        d = self.doc
        self.assertEqual(d["readAt"], LISTINGS["asOf"])
        self.assertEqual(d["kind"], "listings")
        self.assertEqual(d["homes"], 4)
        seen = [r.get("offAt") or r.get("firstSeen") for r in d["records"]]
        self.assertEqual(seen, sorted(seen, reverse=True))
        self.assertEqual(d["records"][0]["address"], "1 Alpha St")
        for r in d["records"]:
            self.assertTrue({"date", "price", "address", "type", "verdict", "tag", "reason", "publishers", "links"} <= set(r))
            self.assertIn(r["verdict"], ("keep", "drop"))

    def test_limit(self):
        rows = [home(f"https://www.movoto.com/{i}/", f"{i} Long St", "2026-11-20", 100000 + i, f"2026-11-{10 + i % 10:02d}T00:00:00+00:00",
                     "2026-11-24T00:00:00+00:00", [source("movoto", "Movoto", f"https://www.movoto.com/{i}/", "2026-11-24T00:00:00+00:00", 100000 + i)])
                for i in range(90)]
        doc = li.build("philadelphia", {**LISTINGS, "rows": rows, "offMarket": {}}, {}, {}, NOW)
        self.assertEqual(len(doc["records"]), li.RECORDS)
        self.assertEqual(doc["homes"], 90)

    def test_verdicts(self):
        a, b, c, d = (self.by_address[k] for k in ("1 Alpha St", "2 Beta St", "3 Gamma St", "4 Delta St"))
        self.assertEqual((a["verdict"], a["tag"], a["reason"], a["publishers"]), ("keep", "new", "new", ["Movoto"]))
        self.assertEqual((b["tag"], b["reason"], b["publishers"]), ("merged", "merged · 2 sites", ["Movoto", "Weichert"]))
        self.assertEqual(b["links"], ["https://www.movoto.com/b/", "https://www.weichert.com/b/"])
        self.assertEqual((c["tag"], c["was"]), ("price", 265000))
        self.assertEqual(c["reason"], "price changed, was $265k · merged · 2 sites")
        self.assertEqual(d["tag"], "unseen")
        self.assertIn("not seen for 23 days", d["reason"])
        self.assertEqual(a["type"], "Single family")

    def test_off_market(self):
        gone = self.by_address["9 Omega St"]
        self.assertEqual((gone["verdict"], gone["tag"], gone["reason"]), ("drop", "off", "no longer for sale"))
        self.assertEqual(gone["links"], ["https://www.movoto.com/gone-1/"])
        self.assertEqual(self.doc["offMarket"], {"count": 2, "shown": 1})   # gone-2 has no address on file: counted only
        self.assertFalse(any("gone-2" in u for r in self.doc["records"] for u in r["links"]))

    def test_publisher_states(self):
        p = {e["provider"]: e for e in self.doc["publishers"]}
        self.assertEqual((p["movoto"]["state"], p["movoto"]["rows"], p["movoto"]["name"]), ("partial", 3, "Movoto"))
        self.assertEqual(p["weichert"]["state"], "ok")
        self.assertEqual((p["everyhome"]["state"], p["everyhome"]["code"], p["everyhome"]["until"]),
                         ("refused", 403, "2026-11-25T09:00:00+00:00"))
        self.assertEqual(p["coldwell_banker"]["state"], "failed")
        self.assertEqual((p["trulia"]["state"], p["trulia"]["code"]), ("paused", 403))
        self.assertEqual(p["northrop"]["state"], "idle")
        self.assertNotIn("onekey", p)          # does not cover Philadelphia
        self.assertEqual(self.doc["skipped"], {"date": 12, "price": 1})
        later = li.build("philadelphia", LISTINGS, STATE, TAGS, datetime(2026, 11, 26, tzinfo=timezone.utc))
        self.assertNotEqual({e["provider"]: e for e in later["publishers"]}["everyhome"]["state"], "refused")

    def test_nothing_forbidden(self):
        text = json.dumps(self.doc)
        for word in ("photos", "description", "broker", "summary", "Jane", "Sunny", "lastError", "search", "Home-Search"):
            self.assertNotIn(word, text)
        self.assertFalse(re.search(r"https?://[^\"\s]+\.(?:jpe?g|png|gif|webp)\b", text, re.I))

    def test_written_next_to_cards(self):
        with tempfile.TemporaryDirectory() as site, tempfile.TemporaryDirectory() as harvested:
            write(harvested, "philadelphia/listings.json", LISTINGS)
            write(harvested, "philadelphia/harvest-state.json", STATE)
            write(site, "philadelphia/listing-tags.json", TAGS)
            lc.publish(site, "philadelphia", now=NOW, log=lambda *_: None, listings_root=Path(harvested))
            path = Path(site, "philadelphia", "listings-intake.json")
            doc = json.loads(path.read_text())
            self.assertEqual(len(doc["records"]), 5)
            self.assertFalse(Path(site, "philadelphia", "listings.json").exists())
            self.assertEqual(oct(path.stat().st_mode & 0o777), "0o644")
            from engine import photo_hold
            self.assertEqual(photo_hold.check_published(site, hold_root=Path(site, "no-hold")), [])

    def test_no_listings_no_file(self):
        with tempfile.TemporaryDirectory() as site:
            self.assertIsNone(li.build_for(site, "philadelphia", NOW))
            lc.publish(site, "philadelphia", now=NOW, log=lambda *_: None)
            self.assertFalse(Path(site, "philadelphia", "listings-intake.json").exists())

    def test_read_source_counts_skips(self):
        class Source:
            NAME = "Test"
            MAX_PAGES = 1

            @staticmethod
            def search_url(city, n):
                return "https://example.com/s" if n == 1 else None

            @staticmethod
            def search(page, city, zone):
                return [{"url": "u1", "date": "2026-11-23", "price": 200000}, {"url": "u2", "date": "2026-01-01", "price": 200000},
                        {"url": "u3", "date": "2026-11-23", "price": None}]

        out = feed.read_source(Source, "philadelphia", NOW, lambda url: None, lambda _: None)
        self.assertEqual(out["skipped"], {"date": 1, "price": 1})
        self.assertEqual([r["url"] for r in out["rows"]], ["u1"])


if __name__ == "__main__":
    unittest.main()
