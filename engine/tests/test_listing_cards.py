import json
import math
import random
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from engine import listing_cards as lc
from engine import model

NOW = datetime(2026, 11, 24, 12, tzinfo=timezone.utc)

PREDICTOR = {
    "intercept": 12.0, "level": {"quarters": ["2026 Q1", "2026 Q2"], "effect": 0.1}, "areaMean": 0.05, "pooled": 0.0,
    "areas": {"Fishtown|": 0.3},
    "log": {"Floor area": {"beta": 0.5, "centre": math.log(1200), "missing": None}},
    "cat": {"Basement": {"ref": "Full, other", "levels": {"Full, finished": math.log(1.08), "Not recorded": -0.1}},
            "Garage": {"ref": "None", "levels": {"1": math.log(1.06)}}},
    "error": {"model": {"mdape": 21.0}, "ppsf": {"mdape": 25.0}},
    "errorByBand": [{"label": "$200k-350k", "model": {"mdape": 18.0}, "ppsf": {"mdape": 20.0}}],
    "bands": [[0, 200000, "Under $200k"], [200000, 350000, "$200k-350k"], [350000, 10 ** 12, "$350k+"]],
}

AREAS = {"through": "2026-07", "areas": [
    {"key": "Fishtown|", "name": "Fishtown", "lat": 39.97, "lon": -75.13, "zips": ["19125"], "sales12": 400,
     "median12": 300000, "ppsf12": 250, "topType": "Single family", "topTypeShare": 95, "priceGrowth1": 8.0,
     "violentRate": 4.0, "propertyRate": 30.0},
    {"key": "Kensington|", "name": "Kensington", "lat": 39.99, "lon": -75.12, "zips": ["19134"], "sales12": 300,
     "median12": 150000, "ppsf12": 140, "topType": "Single family", "topTypeShare": 97, "medianYoY": -1.0,
     "violentRate": 12.0, "propertyRate": 50.0},
]}


def write(root, path, obj):
    p = Path(root, path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj))


def row(url, address, date, price, **facts):
    return {"key": lc.normalize_address(address, "philadelphia") and address.upper(), "url": url, "address": address,
            "date": date, "price": price, "source": "Movoto", "sources": [{"provider": "movoto", "source": "Movoto", "url": url}],
            **facts}


class FakeStore:
    """The two store reads the cards need: the characteristics lookup and the stored sales."""

    def __init__(self, lookup, rows):
        self.lookup, self.rows = lookup, rows

    def read_lookup(self, name):
        return self.lookup if name == "philadelphia_characteristics" else {}

    def iter_rows(self, city):
        return iter(self.rows)

    def boundary_path(self, city):
        return "/nonexistent/boundaries.geojson"


class EstimateTests(unittest.TestCase):
    def test_parts_multiply_out_to_the_estimate(self):
        f = {"Floor area": math.log(1500), "Basement": "Full, finished", "Garage": None}
        est = lc.model_estimate(PREDICTOR, f, "Fishtown|", "Fishtown")
        expected = math.exp(12.0 + 0.1 + 0.3 + 0.5 * (math.log(1500) - math.log(1200)) + math.log(1.08))
        self.assertAlmostEqual(est["value"], expected, places=3)
        product = est["base"] * (1 + est["area"]["pct"] / 100)
        for p in est["parts"]:
            product *= 1 + p["pct"] / 100
        self.assertAlmostEqual(product / est["value"], 1, places=2)
        labels = [p["label"] for p in est["parts"]]
        self.assertIn("Finished basement", labels)
        self.assertIn("Bigger than typical", labels)
        self.assertFalse(any("Garage" in lab for lab in labels))   # an unrecorded level with no column adds nothing

    def test_unseen_area_takes_the_pooled_effect_and_no_area_gives_none(self):
        f = {"Floor area": None, "Basement": None, "Garage": None}
        est = lc.model_estimate(PREDICTOR, f, "Nowhere|", "Nowhere")
        self.assertAlmostEqual(est["value"], math.exp(12.0 + 0.1 + 0.0 - 0.1), places=3)
        self.assertTrue(any(p.get("notRecorded") for p in est["parts"]))
        self.assertIsNone(lc.model_estimate(PREDICTOR, f, None, None))

    def test_band_error_falls_back_to_overall(self):
        self.assertEqual(lc.band_error(PREDICTOR, "model", 250000), (18.0, "$200k-350k"))
        self.assertEqual(lc.band_error(PREDICTOR, "model", 900000), (21.0, None))

    def test_nice_rounding_never_reads_precise(self):
        self.assertEqual(lc.nice(204_321), 205_000)
        self.assertEqual(lc.nice(87_640), 88_000)
        self.assertEqual(lc.nice(1_234_567), 1_225_000)


class PredictorExportTests(unittest.TestCase):
    def test_predictor_reproduces_the_fit(self):
        rnd = random.Random(1)
        spec = [{"name": "Floor area", "kind": "log"},
                {"name": "Condition", "kind": "cat", "ref": "Average", "order": ["Rehabbed", "Average"]}]
        recs = []
        for i in range(1500):
            q = rnd.choice(["2025 Q1", "2025 Q2", "2025 Q3", "2025 Q4"])
            a, c, s = rnd.choice("ABC"), rnd.choice(["Rehabbed", "Average"]), rnd.uniform(800, 2500)
            y = 11 + 0.7 * math.log(s) + (0.2 if c == "Rehabbed" else 0) + {"A": 0, "B": 0.3, "C": -0.2}[a] + rnd.gauss(0, 0.05)
            recs.append({"id": f"r{i}", "y": y, "area": a, "period": q, "f": {"Floor area": math.log(s), "Condition": c}})
        fitted = model.fit(model.Design(spec, recs), recs, robust=False)
        pred = model.predictor_of(fitted, through="2025-09")
        self.assertEqual(pred["level"]["quarters"], ["2025 Q2", "2025 Q3"])     # Q4 ends after "through"
        f = {"Floor area": math.log(1500), "Condition": "Rehabbed"}
        est = lc.model_estimate(pred, f, "B", "B")
        direct = model.predict(fitted, [{"id": "x", "area": "B", "period": "2030 Q1", "f": f}], period_effect=pred["level"]["effect"])[0]
        self.assertAlmostEqual(math.log(est["value"]), direct, places=4)
        json.dumps(pred)   # JSON-ready


class BuildTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        write(self.root, "philadelphia/areas.json", AREAS)
        write(self.root, "philadelphia/overview.json", {"repeatIndex": {"growth1": 3.0}, "types": {}})
        write(self.root, "index.json", {"cities": [{"id": "philadelphia", "kpi": {"medianYoY": 2.0, "ppsf": 200},
                                                    "context": {"violentRate": 7.0, "propertyRate": 38.0}}]})
        rows = [
            row("https://x/1", "100 Main Street, Philadelphia, PA 19125", "2026-11-20", 250000, lat=39.971, lon=-75.131,
                sqft=1200, beds=3, baths=1, type="Row/Townhouse", zip="19125"),
            row("https://x/2", "5 Oak Street, Philadelphia, PA 19134", "2026-11-23", 120000, sqft=1000, type="Twin", zip="19134"),
            row("https://x/3", "7 Elm Street #2, Philadelphia, PA 19125", "2026-11-22", 400000, lat=39.97, lon=-75.13,
                sqft=900, type="Condo", zip="19125"),
            row("https://x/4", "9 Ash Street, Philadelphia, PA 19125", "2026-11-21", 900000, lat=39.97, lon=-75.13,
                sqft=200, type="Single Family", zip="19125"),
        ]
        write(self.root, "philadelphia/listings.json", {"asOf": "2026-11-24T00:00:00+00:00", "from": "2026-11-17",
                                                         "through": "2026-11-23", "publishers": {}, "rows": rows})
        write(self.root, "philadelphia/listing-tags.json", {"photoTagging": True, "retentionDays": 30, "listings": [
            {"url": "https://x/2", "urls": ["https://x/2"], "signals": {"as_is": "AS-IS", "renovated": "fully renovated"},
             "description": {"unusual": True},
             "photo": {"findingCount": 3, "credit": "Movoto", "creditUrl": "https://x/2",
                       "findings": [{"type": "water_stain_ceiling", "room": "bedroom", "rooms": 2, "evidence": "stain",
                                     "crops": ["analysis/crops/philadelphia/a.jpg", "analysis/crops/philadelphia/b.jpg"]},
                                    {"type": "peeling_paint", "room": "kitchen", "rooms": 1, "evidence": "paint", "crops": []}],
                       "coverage": {"rating": "partial", "missing": ["basement"]}, "editing": {}}},
            {"url": "https://x/old", "urls": ["https://x/old"], "inSnapshot": False, "address": "1 Old St, Philadelphia, PA 19125",
             "date": "2026-10-30", "price": 200000, "source": "Weichert", "signals": {}, "compare": {"area": "Fishtown"}},
        ]})

    def tearDown(self):
        self.tmp.cleanup()

    def cards(self, store=None):
        doc = lc.build(self.root, "philadelphia", store, NOW)
        return doc, {c["url"]: c for c in doc["cards"]}

    def test_newest_first_with_pins_only_where_coordinates_exist(self):
        doc, by = self.cards()
        self.assertEqual([c["url"] for c in doc["cards"]], ["https://x/2", "https://x/3", "https://x/4", "https://x/1", "https://x/old"])
        self.assertNotIn("lat", by["https://x/2"])
        self.assertEqual(doc["summary"]["withPin"], 3)
        self.assertEqual(by["https://x/old"]["inSnapshot"], False)
        self.assertEqual(by["https://x/old"]["area"]["key"], "Fishtown|")

    def test_area_and_sold_comparison(self):
        _, by = self.cards()
        c = by["https://x/1"]
        self.assertEqual(c["area"]["key"], "Fishtown|")
        self.assertEqual(c["sold"]["ppsf12"], 250)
        self.assertAlmostEqual(c["sold"]["vsPpsfPct"], round(100 * (250000 / 1200 / 250 - 1), 1))
        self.assertEqual(c["estimate"]["kind"], "ppsf")
        self.assertEqual(c["estimate"]["value"], 300000)
        self.assertIsNone(c["estimate"]["error"])          # no backtest without a model file: no range is claimed
        self.assertNotIn("low", c["estimate"])
        self.assertEqual(by["https://x/2"]["area"]["key"], "Kensington|")   # ZIP code, no coordinates

    def test_condos_get_no_ppsf_and_the_median_only_for_the_areas_main_type(self):
        _, by = self.cards()
        condo = by["https://x/3"]
        self.assertNotIn("vsPpsfPct", condo["sold"])
        self.assertNotIn("estimate", condo)                # the area's sales are single family; a condo gets no median
        odd = by["https://x/4"]                            # 200 sq ft for $900k: the floor area is not believable
        self.assertNotIn("vsPpsfPct", odd["sold"])
        self.assertEqual(odd["estimate"]["kind"], "median")    # the median does not use the floor area

    def test_cards_without_an_estimate_say_why(self):
        _, by = self.cards()
        self.assertEqual(by["https://x/3"]["noEstimate"], "No sold homes of this type nearby to compare")   # a condo
        doc = json.loads(Path(self.root, "philadelphia/listings.json").read_text())
        doc["rows"][3]["type"] = "Condo"      # 200 sq ft and not the area's main type: nothing to fall back on
        doc["rows"][1].pop("sqft")
        doc["rows"][1]["type"] = "Condo"
        write(self.root, "philadelphia/listings.json", doc)
        _, by = self.cards()
        self.assertEqual(by["https://x/4"]["noEstimate"], "No sold homes of this type nearby to compare")
        self.assertNotIn("estimate", by["https://x/4"])

    def test_items_are_split_into_pluses_and_minuses(self):
        _, by = self.cards()
        c = by["https://x/2"]
        plus, minus = [i["text"] for i in c["plus"]], [i["text"] for i in c["minus"]]
        self.assertIn("Renovated", plus)
        self.assertIn("Sold as-is", minus)
        self.assertIn("Water-stained ceiling · 2 rooms", minus)
        self.assertIn("Not shown in photos: basement", minus)
        self.assertIn("More incidents than the city figure", minus)
        self.assertIn("Prices here -1% in a year (city +3%)", minus)
        self.assertIn("$/sq ft 14% under sold homes here", plus)
        self.assertEqual(c["signals"], ["as_is", "renovated"])
        self.assertEqual(c["photo"]["findings"], 3)
        self.assertEqual([x["src"] for x in c["photo"]["crops"]], ["analysis/crops/philadelphia/a.jpg", "analysis/crops/philadelphia/b.jpg"])
        fish = by["https://x/1"]
        self.assertIn("Fewer incidents than the city figure", [i["text"] for i in fish["plus"]])
        words = json.dumps([by[u]["plus"] + by[u]["minus"] for u in by]).lower()
        for banned in ("safe", "dangerous", "good area", "bad area"):
            self.assertNotIn(banned, words)

    def test_no_photo_items_outside_photo_cities(self):
        tags = json.loads(Path(self.root, "philadelphia/listing-tags.json").read_text())
        tags["photoTagging"] = False
        write(self.root, "philadelphia/listing-tags.json", tags)
        _, by = self.cards()
        self.assertNotIn("photo", by["https://x/2"])
        self.assertNotIn("Water-stained ceiling · 2 rooms", [i["text"] for i in by["https://x/2"]["minus"]])

    def test_model_estimate_for_a_matched_parcel(self):
        write(self.root, "philadelphia/model-predictor.json", PREDICTOR)
        store = FakeStore({"P1": {"sqft": "1500", "bsmt": "A", "garage": "1"}},
                          [{"id": "s1", "date": "2025-06-01", "price": "120000", "type": "Single family",
                            "address": "100 Main St", "zip": "19125", "sqft": "1500", "year_built": "", "beds": "3",
                            "parcel": "P1"}])
        doc, by = self.cards(store)
        c = by["https://x/1"]
        e = c["estimate"]
        self.assertEqual(e["kind"], "model")
        self.assertLess(e["low"], e["value"])
        self.assertLess(e["value"], e["high"])
        self.assertEqual(e["error"], 18.0 if 200000 <= e["value"] < 350000 else 21.0)
        self.assertIn("Finished basement", [i["text"] for i in c["plus"]])
        self.assertEqual(c["lastSale"], {"date": "2025-06-01", "price": 120000, "recentResale": True})
        self.assertEqual(doc["summary"]["modelled"], 1)
        self.assertEqual(by["https://x/2"]["estimate"]["kind"], "ppsf")        # no parcel match: area $/sq ft
        self.assertEqual(by["https://x/2"]["estimate"]["error"], 25.0)         # the ppsf rule's own backtest error

    def test_first_and_last_seen_reach_the_card(self):
        doc = json.loads(Path(self.root, "philadelphia/listings.json").read_text())
        doc["rows"][0].update(firstSeen="2026-11-01T03:00:00+00:00", lastSeen="2026-11-05T03:00:00+00:00")
        write(self.root, "philadelphia/listings.json", doc)
        _, by = self.cards()
        self.assertEqual((by["https://x/1"]["firstSeen"], by["https://x/1"]["lastSeen"]), ("2026-11-01", "2026-11-05"))
        self.assertNotIn("lastSeen", by["https://x/2"])

    def test_detail_files_hold_findings_quotes_and_nearby_sales(self):
        write(self.root, "philadelphia/sales.json", {"typeNames": ["Single family", "Condo"], "columns": {
            "date": ["2026-06-01", "2026-07-01", "2026-05-01"], "price": [240000, 260000, 500000],
            "address": ["102 Main St", "100 Main St", "9 Far Rd"], "type": [0, 0, 0], "area": [0, 0, 1],
            "sqft": [1100, 1200, 1500], "lat": [39.9712, 39.9711, 39.99], "lon": [-75.1311, -75.1311, -75.12]}})
        doc = lc.publish(self.root, "philadelphia", now=NOW, log=lambda *_: None)
        by = {c["url"]: c for c in doc["cards"]}
        folder = Path(self.root, "philadelphia/listing-detail")
        self.assertEqual(len(list(folder.glob("*.json"))), len(by))
        d1 = json.loads((folder / f"{by['https://x/1']['detail']}.json").read_text())
        self.assertEqual([c["address"] for c in d1["comps"]], ["102 Main St"])      # its own address is not a comparison
        d2 = json.loads((folder / f"{by['https://x/2']['detail']}.json").read_text())
        self.assertEqual(d2["photo"]["findings"][0]["crops"], ["analysis/crops/philadelphia/a.jpg", "analysis/crops/philadelphia/b.jpg"])
        self.assertEqual(d2["photo"]["missing"], ["basement"])
        self.assertIn({"signal": "as_is", "label": "Sold as-is", "quote": "AS-IS"}, d2["quotes"])
        self.assertEqual(d2["comps"][0]["address"], "9 Far Rd")                       # no pin: the area's latest sales
        self.assertNotIn("_details", json.loads(Path(self.root, "philadelphia/listing-cards.json").read_text()))
        (folder / "stale.json").write_text("{}")
        lc.publish(self.root, "philadelphia", now=NOW, log=lambda *_: None)
        self.assertFalse((folder / "stale.json").exists())

    def test_publish_writes_the_file_and_skips_cities_without_listings(self):
        doc = lc.publish(self.root, "philadelphia", now=NOW, log=lambda *_: None)
        self.assertEqual(json.loads(Path(self.root, "philadelphia/listing-cards.json").read_text())["summary"], doc["summary"])
        self.assertIsNone(lc.publish(self.root, "nyc", now=NOW, log=lambda *_: None))
        lc.refresh(self.root, ["nyc"], store_dir=None, log=lambda *_: None)   # never raises


if __name__ == "__main__":
    unittest.main()
