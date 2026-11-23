import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from engine import live_listings as feed


def item(slug, posted, **changes):
    offer = {"availability": "https://schema.org/InStock", "price": 200000, "priceCurrency": "USD",
             "url": feed.HOST + "/home/" + slug,
             "itemOffered": {"address": {"streetAddress": "123 Sample St", "addressLocality": "Chicago",
                                         "addressRegion": "IL"}}}
    return {"item": {"@type": "RealEstateListing", "datePosted": posted, "offers": [{**offer, **changes}]}}


def page(items, next_path=""):
    collection = {"@type": "CollectionPage", "mainEntity": {"itemListElement": items}}
    return f'<script type="application/ld+json">{json.dumps(collection)}</script><a href="{next_path}">Next</a>'


class ListingTests(unittest.TestCase):
    now = datetime(2026, 11, 24, 2, tzinfo=timezone.utc)
    first = "/for_sale/Chicago,IL/new-listings/"
    second = first + "2_p/"

    def collect(self, pages):
        return feed.collect("chicago", self.now, pages.__getitem__, lambda _: None)

    def test_published_date_and_pagination(self):
        first = item("first", "2026-11-24T01:30:00Z")
        data = self.collect({self.first: page([first], self.second), self.second: page([
            first, item("second", "2026-11-17"), item("old", "2026-11-16"), item("future", "2026-11-25"),
            item("sold", "2026-11-23", availability="SoldOut"), item("missing-price", "2026-11-23", price=None),
            item("bad-link", "2026-11-23", url="https://example.com/"), item("no-date", None),
        ])})
        self.assertFalse(data["partial"])
        self.assertEqual(data["pages"], 2)
        self.assertEqual([r["date"] for r in data["rows"]], ["2026-11-23", "2026-11-17"])
        self.assertEqual(data["rows"][0]["url"], feed.HOST + "/home/first")

    def test_partial_and_unavailable_are_distinct_from_empty(self):
        self.assertEqual(self.collect({self.first: page([])})["rows"], [])
        data = self.collect({self.first: page([item("first", "2026-11-23")], self.second),
                             self.second: "<html>challenge</html>"})
        self.assertTrue(data["partial"])
        self.assertEqual(len(data["rows"]), 1)
        with self.assertRaisesRegex(ValueError, "Missing listings"):
            self.collect({self.first: "<html>unexpected</html>"})
        with tempfile.TemporaryDirectory() as directory, patch.object(feed, "collect", side_effect=OSError("offline")):
            self.assertFalse(feed.publish(Path(directory), "chicago"))
            self.assertEqual(json.loads((Path(directory) / "chicago/new-listings.json").read_text()),
                             {"status": "unavailable"})
