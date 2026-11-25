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
                                     "crops": ["analysis/crops/philadelphia/a.jpg", "analysis/crops/philadelphia/b.jpg"],
                                     "points": [{"x": 0.3, "y": 0.6}, None]},
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
        # the median does not use the floor area, but $900k is over twice it: no number is shown (ATYPICAL_RATIO)
        self.assertNotIn("estimate", odd)
        self.assertEqual(odd["noEstimate"], "Floor area on the listing looks wrong")

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
        self.assertEqual([x["point"] for x in c["photo"]["crops"]], [{"x": 0.3, "y": 0.6}, None])   # parallel to src
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
        self.assertEqual(d2["photo"]["findings"][0]["points"], [{"x": 0.3, "y": 0.6}, None])
        self.assertEqual(d2["photo"]["findings"][1]["points"], [])                    # no crops, no points
        self.assertEqual(d2["photo"]["missing"], ["basement"])
        self.assertIn({"signal": "as_is", "label": "Sold as-is", "quote": "AS-IS"}, d2["quotes"])
        self.assertEqual(d2["comps"][0]["address"], "9 Far Rd")                       # no pin: the area's latest sales
        self.assertNotIn("_details", json.loads(Path(self.root, "philadelphia/listing-cards.json").read_text()))
        (folder / "stale.json").write_text("{}")
        lc.publish(self.root, "philadelphia", now=NOW, log=lambda *_: None)
        self.assertFalse((folder / "stale.json").exists())

    def test_seeded_homes_get_plain_cards(self):
        doc = json.loads(Path(self.root, "philadelphia/listings.json").read_text())
        doc["rows"].append({"key": "8 PINE ST 19125", "url": "https://x/seed", "address": "8 Pine St, Philadelphia, PA 19125",
                            "date": "2026-11-18", "price": 270000, "source": "Movoto", "sqft": 1100, "beds": 3, "zip": "19125",
                            "lat": 39.971, "lon": -75.131, "seeded": True, "lastSeen": "2026-11-18T01:00:00+00:00",
                            "sources": [{"provider": "movoto", "source": "Movoto", "url": "https://x/seed"}]})
        write(self.root, "philadelphia/listings.json", doc)
        d, by = self.cards()
        c = by["https://x/seed"]
        self.assertEqual(c["estimate"]["kind"], "ppsf")
        self.assertNotIn("photo", c)
        self.assertNotIn("signals", c)
        self.assertEqual(c["lastSeen"], "2026-11-18")
        self.assertEqual(d["_details"][c["detail"]]["id"], c["id"])

    def test_publish_writes_the_file_and_skips_cities_without_listings(self):
        doc = lc.publish(self.root, "philadelphia", now=NOW, log=lambda *_: None)
        self.assertEqual(json.loads(Path(self.root, "philadelphia/listing-cards.json").read_text())["summary"], doc["summary"])
        self.assertIsNone(lc.publish(self.root, "nyc", now=NOW, log=lambda *_: None))
        lc.refresh(self.root, ["nyc"], store_dir=None, log=lambda *_: None)   # never raises


def opa_row(p, loc, unit=None, zip_code="19134", cat="SINGLE FAMILY", bdesc="ROW PORCH FRONT", lat=39.99, lng=-75.12, **f):
    """One opa_properties_public row as PHL_BY_LOCATION_SQL selects it."""
    return {"p": p, "loc": loc, "unit": unit, "zip": zip_code, "cat": cat, "bdesc": bdesc, "bdesc_old": "ROW 2 STY MASONRY",
            "lat": lat, "lng": lng,
            "ic": "4", "ec": "4", "baths": 1, "beds": 3, "stories": 2, "sqft": 1100, "lot": 900, "garage": 0, "air": "N",
            "bsmt": "D", "grade": "C", "vw": "I", "fp": 0, "yb": 1925, "mv": 150000, **f}


class FakeCarto:
    """Answers PHL_BY_LOCATION_SQL from {location: [rows]}; records every query. `fail` raises like an unreachable API."""

    def __init__(self, parcels=None, fail=False):
        self.parcels, self.fail, self.queries = parcels or {}, fail, []

    def get_json(self, url, params, headers=None):
        from engine.http import FetchError
        self.queries.append(params["q"])
        if self.fail:
            raise FetchError("phl.carto.com failed after 2 attempts: URLError")
        import re
        inside = re.search(r"location in \((.*)\)$", params["q"]).group(1)
        return {"rows": [r for loc in re.findall(r"'([^']*)'", inside) for r in self.parcels.get(loc, [])]}


class RootedStore(FakeStore):
    def __init__(self, root, lookup=None, rows=None):
        super().__init__(lookup or {}, rows or [])
        self.root = str(root)


class AddressLookupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = RootedStore(self.tmp.name)
        self.today = "2026-11-24"

    def tearDown(self):
        self.tmp.cleanup()

    def records(self, client, addresses, today=None, log=None):
        return lc.AddressRecords(self.store, "philadelphia", client, today or self.today,
                                 log or (lambda *_: None)).prepare(addresses)

    def test_locations_are_built_strictly_and_hostile_text_is_never_sent(self):
        from engine.characteristics import phl_location, phl_records_by_location
        key = lambda a: lc.normalize_address(a, "philadelphia")
        self.assertEqual(phl_location(key("946 N 2nd St #3 Philadelphia, PA 19123")), "946 N 2ND ST")
        self.assertEqual(phl_location(key("1214 South 21st Street, Philadelphia, PA 19146")), "1214 S 21ST ST")
        self.assertEqual(phl_location(key("40 W 11th St")), "40 W 11TH ST")
        self.assertEqual(phl_location(key("5 E 103rd St")), "5 E 103RD ST")
        for hostile in ["1 Main St'); DROP TABLE opa_properties_public;-- , Philadelphia, PA 19125",
                        "7 '||pg_sleep(9)||' St", "12 O'Neil St", "3 Elm St\u0000 or 1=1", "9 Ash St; select owner_1"]:
            self.assertIsNone(phl_location(key(hostile)), hostile)
        client = FakeCarto()
        with self.assertRaises(ValueError):
            phl_records_by_location(client, ["1 MAIN ST') OR ('1'='1"])
        self.assertEqual(client.queries, [])
        recs = self.records(client, ["1 Main St'); DROP TABLE opa_properties_public;--", "100 Main Street, Philadelphia, PA 19125"])
        self.assertEqual(len(client.queries), 1)
        q = client.queries[0]
        self.assertIn("location in ('100 MAIN ST')", q)
        self.assertNotIn("DROP", q)
        for banned in ("owner", "mailing"):
            self.assertNotIn(banned, q.lower())
        self.assertIn("ST_Y(the_geom) lat, ST_X(the_geom) lng", q)      # the parcel's position, for a pin
        self.assertNotIn("category_code_description in", q)            # every category: a pin needs no record
        self.assertEqual(recs.match("1 Main St'); DROP TABLE opa_properties_public;--"), (None, None))

    def test_addresses_are_batched_into_few_queries(self):
        client = FakeCarto({"5 OAK ST": [opa_row("P5", "5 OAK ST")]})
        addresses = [f"{n} Oak Street, Philadelphia, PA 19134" for n in range(1, 251)] + ["5 Oak St, Philadelphia, PA 19134"]
        recs = self.records(client, addresses)
        self.assertEqual(len(client.queries), 3)                     # 250 distinct addresses, 100 per query
        sale, record = recs.match("5 Oak St, Philadelphia, PA 19134")
        self.assertIsNone(sale)
        self.assertEqual((record["sqft"], record["form"], record["type"]), ("1100", "Row", "Single family"))

    def test_ambiguous_addresses_and_condos_do_not_match(self):
        client = FakeCarto({
            "7 ELM ST": [opa_row("A1", "7 ELM ST", zip_code="19125"), opa_row("A2", "7 ELM ST", zip_code="19134")],
            "9 ASH ST": [opa_row("C1", "9 ASH ST", unit="2", bdesc="RES CONDO 3 STY MASON"),
                         opa_row("C2", "9 ASH ST", unit="3", bdesc="RES CONDO 3 STY MASON")],
            "11 FIR ST": [opa_row("F1", "11 FIR ST", unit="1"), opa_row("F2", "11 FIR ST", unit="2")],
        })
        recs = self.records(client, ["7 Elm St", "9 Ash St #2", "11 Fir St"])
        self.assertEqual(recs.match("7 Elm St"), (None, None))                         # two parcels, no ZIP to choose
        self.assertEqual(recs.match("7 Elm St, Philadelphia, PA 19134")[1]["type"], "Single family")   # the ZIP decides
        self.assertEqual(recs.match("9 Ash St #2"), (None, None))                      # a condo: the model has none
        self.assertEqual(recs.match("11 Fir St"), (None, None))                        # units only: which one?
        self.assertIsNotNone(recs.match("11 Fir St #2")[1])

    def test_answers_are_cached_including_no_parcel_and_refreshed_after_30_days(self):
        client = FakeCarto({"5 OAK ST": [opa_row("P5", "5 OAK ST")]})
        self.records(client, ["5 Oak St", "77 Nowhere Ave"])
        self.assertEqual(len(client.queries), 1)
        cached = json.loads(Path(self.tmp.name, "_lookups", "philadelphia_address_records.json").read_text())["addresses"]
        self.assertEqual(cached["77 NOWHERE AVE"], {"v": lc.ADDRESS_CACHE_VERSION, "fetched": self.today, "parcels": []})
        again = FakeCarto(fail=True)                     # a second run asks nothing it already knows
        recs = self.records(again, ["5 Oak St", "77 Nowhere Ave"], log=self.fail)
        self.assertEqual(again.queries, [])
        self.assertIsNotNone(recs.match("5 Oak St")[1])
        later = FakeCarto({})
        self.records(later, ["5 Oak St", "77 Nowhere Ave"], today="2026-12-25")
        self.assertEqual(len(later.queries), 1)
        self.assertIn("'5 OAK ST'", later.queries[0])

    def test_entries_cached_before_positions_are_asked_again_once(self):
        path = Path(self.tmp.name, "_lookups", "philadelphia_address_records.json")
        write(self.tmp.name, "_lookups/philadelphia_address_records.json", {"addresses": {
            "5 OAK ST": {"fetched": self.today, "parcels": [{"p": "P5", "location": "5 OAK ST", "unit": "", "zip": "19134",
                                                             "type": "Single family", "rec": {"sqft": "1100"}}]},
            "77 NOWHERE AVE": {"fetched": self.today, "parcels": []}}})
        client = FakeCarto({"5 OAK ST": [opa_row("P5", "5 OAK ST")]})
        recs = self.records(client, ["5 Oak St", "77 Nowhere Ave"])
        self.assertEqual(len(client.queries), 1)
        self.assertIn("'5 OAK ST'", client.queries[0])
        self.assertIn("'77 NOWHERE AVE'", client.queries[0])
        self.assertEqual(recs.position("5 Oak St"), (39.99, -75.12))
        self.assertEqual(json.loads(path.read_text())["addresses"]["5 OAK ST"]["v"], lc.ADDRESS_CACHE_VERSION)
        again = FakeCarto(fail=True)
        self.records(again, ["5 Oak St", "77 Nowhere Ave"], log=self.fail)   # now current: nothing asked
        self.assertEqual(again.queries, [])

    def test_positions_come_from_any_category_and_are_guarded(self):
        bounds = (39.86, 40.14, -75.29, -74.95)
        client = FakeCarto({
            "1 LOT ST": [opa_row("V1", "1 LOT ST", cat="VACANT LAND", lat=39.98, lng=-75.15)],
            "9 ASH ST": [opa_row("C1", "9 ASH ST", unit="2", bdesc="RES CONDO", lat=39.97, lng=-75.13),
                         opa_row("C2", "9 ASH ST", unit="3", bdesc="RES CONDO", lat=39.9702, lng=-75.1302)],
            "4 FAR ST": [opa_row("F1", "4 FAR ST", zip_code="19134", lat=39.99, lng=-75.12),
                         opa_row("F2", "4 FAR ST", zip_code="19111", lat=40.08, lng=-75.05)],
            "6 OUT ST": [opa_row("O1", "6 OUT ST", lat=40.5, lng=-75.12)],
            "8 NONE ST": [opa_row("N1", "8 NONE ST", lat=None, lng=None)],
        })
        recs = self.records(client, ["1 Lot St", "9 Ash St #2", "4 Far St", "6 Out St", "8 None St"])
        self.assertEqual(recs.position("1 Lot St", bounds=bounds), (39.98, -75.15))       # vacant land: a pin, no record
        self.assertEqual(recs.match("1 Lot St"), (None, None))
        self.assertEqual(recs.position("9 Ash St #2", bounds=bounds), (39.97, -75.13))    # a condo: its own unit
        self.assertEqual(recs.match("9 Ash St #2"), (None, None))                          # ... but still no estimate
        lat, lon = recs.position("9 Ash St", bounds=bounds)                                # no unit: the building
        self.assertAlmostEqual(lat, 39.9701)
        self.assertIsNone(recs.position("4 Far St", bounds=bounds))                        # two places: which one?
        self.assertEqual(recs.position("4 Far St, Philadelphia, PA 19134", bounds=bounds), (39.99, -75.12))   # ZIP decides
        self.assertIsNone(recs.position("4 Far St", "19125", bounds))                      # a parcel in another ZIP
        self.assertIsNone(recs.position("6 Out St", bounds=bounds))                        # outside the city
        self.assertIsNone(recs.position("8 None St", bounds=bounds))                       # no position recorded

    def test_record_only_features_match_the_sale_path(self):
        from engine.characteristics import CITIES
        cfg = CITIES["philadelphia"]
        rec = lc.AddressRecords(self.store, "philadelphia", FakeCarto({"5 OAK ST": [opa_row("P5", "5 OAK ST")]}),
                                self.today, lambda *_: None).prepare(["5 Oak St"]).match("5 Oak St")[1]
        sale = {"type": "Single family", "sqft": "1100", "year_built": "1925", "beds": "3", "date": "2020-01-01"}
        self.assertEqual(lc.model_features(cfg, None, rec, self.today), lc.model_features(cfg, sale, rec, self.today))
        self.assertEqual(lc.model_features(cfg, None, rec, self.today)["Age at sale"], "100-124 yrs")    # today's age
        self.assertIsNone(lc.model_features(cfg, None, {k: v for k, v in rec.items() if k != "type"}, self.today))


class AddressBuildTests(unittest.TestCase):
    """The card build with the lookup: 5 Oak St (Kensington) has not sold within the stored sales."""
    setUp, tearDown = BuildTests.setUp, BuildTests.tearDown

    def build_with(self, client, logs=None):
        write(self.root, "philadelphia/model-predictor.json", dict(PREDICTOR, areas={"Fishtown|": 0.3, "Kensington|": -0.4}))
        store = RootedStore(Path(self.root, "store"), {"P1": {"sqft": "1500"}},
                            [{"id": "s1", "date": "2025-06-01", "price": "120000", "type": "Single family",
                              "address": "100 Main St", "zip": "19125", "sqft": "1500", "year_built": "", "beds": "3",
                              "parcel": "P1"}])
        doc = lc.build(self.root, "philadelphia", store, NOW, client=client, log=(logs.append if logs is not None else print))
        return doc, {c["url"]: c for c in doc["cards"]}

    def test_a_home_with_no_recorded_sale_is_priced_from_its_record(self):
        client = FakeCarto({"5 OAK ST": [opa_row("P5", "5 OAK ST", bsmt="A")]})
        doc, by = self.build_with(client)
        c = by["https://x/2"]
        self.assertEqual(c["estimate"]["kind"], "model")
        self.assertIn("Finished basement", [p["label"] for p in c["estimate"]["parts"]])
        self.assertEqual(c["estimate"]["area"]["name"], "Kensington")
        self.assertNotIn("lastSale", c)                                  # it has none, and the card does not claim one
        self.assertEqual(set(c["estimate"]) - {"low", "high", "vsPct"},
                         set(by["https://x/1"]["estimate"]) - {"low", "high", "vsPct"})   # the shape the site reads
        self.assertEqual(by["https://x/1"]["lastSale"]["date"], "2025-06-01")            # the sold home is unchanged
        self.assertEqual(doc["summary"]["estimates"]["model"], 2)
        q = " ".join(client.queries)
        self.assertNotIn("100 MAIN ST", q)            # matched to a recorded sale already: not asked
        self.assertNotIn("7 ELM ST", q)               # a condo listing: the model has no condos, so not asked

    def test_a_listing_without_coordinates_gets_the_parcels_position(self):
        client = FakeCarto({"5 OAK ST": [opa_row("P5", "5 OAK ST", lat=39.991, lng=-75.121)]})
        doc, by = self.build_with(client)
        c = by["https://x/2"]
        self.assertEqual((c["lat"], c["lon"], c["pinFrom"]), (39.991, -75.121, "parcel"))
        self.assertEqual((by["https://x/1"]["lat"], by["https://x/1"]["lon"]), (39.971, -75.131))   # the publisher's
        self.assertNotIn("pinFrom", by["https://x/1"])
        self.assertNotIn("pinFrom", by["https://x/3"])
        self.assertEqual(doc["summary"]["withPin"], 4)

    def test_a_recorded_sales_position_comes_before_the_parcels(self):
        write(self.root, "philadelphia/listings.json", {"asOf": "2026-11-24T00:00:00+00:00", "rows": [
            row("https://x/1", "100 Main Street, Philadelphia, PA 19125", "2026-11-20", 250000, zip="19125"),
            row("https://x/5", "200 Main Street, Philadelphia, PA 19125", "2026-11-20", 250000, zip="19125"),
            row("https://x/6", "300 Main Street, Philadelphia, PA 19125", "2026-11-20", 250000, zip="19125",
                lat=39.95, lon=-75.16)]})
        client = FakeCarto({"100 MAIN ST": [opa_row("P1", "100 MAIN ST", zip_code="19125", lat=39.9, lng=-75.2)],
                            "200 MAIN ST": [opa_row("P2", "200 MAIN ST", zip_code="19125", lat=39.9, lng=-75.2)],
                            "300 MAIN ST": [opa_row("P3", "300 MAIN ST", zip_code="19125", lat=39.9, lng=-75.2)]})
        sales = [{"id": "s1", "date": "2025-06-01", "price": "120000", "type": "Single family", "address": "100 Main St",
                  "zip": "19125", "lat": "39.97123", "lon": "-75.13456", "parcel": "P1"},
                 {"id": "s2", "date": "2025-06-01", "price": "120000", "type": "Single family", "address": "200 Main St",
                  "zip": "19125", "lat": "41.5", "lon": "-75.13456", "parcel": "P2"},       # outside the city
                 {"id": "s3", "date": "2025-06-01", "price": "120000", "type": "Single family", "address": "300 Main St",
                  "zip": "19125", "lat": "39.97", "lon": "-75.13", "parcel": "P3"}]
        write(self.root, "philadelphia/model-predictor.json", PREDICTOR)
        store = RootedStore(Path(self.root, "store"), {k: {"sqft": "1500"} for k in ("P1", "P2", "P3")}, sales)
        doc = lc.build(self.root, "philadelphia", store, NOW, client=client, log=self.fail)
        by = {c["url"]: c for c in doc["cards"]}
        self.assertEqual((by["https://x/1"]["lat"], by["https://x/1"]["pinFrom"]), (39.97123, "sale"))
        self.assertEqual((by["https://x/5"]["lat"], by["https://x/5"]["pinFrom"]), (39.9, "parcel"))   # sale rejected
        self.assertEqual((by["https://x/6"]["lat"], by["https://x/6"]["lon"]), (39.95, -75.16))      # publisher wins
        self.assertNotIn("pinFrom", by["https://x/6"])
        q = " ".join(client.queries)
        self.assertNotIn("100 MAIN ST", q)            # its recorded sale gave both the record and the pin
        self.assertIn("200 MAIN ST", q)
        self.assertNotIn("300 MAIN ST", q)

    def test_the_fallback_position_places_the_home_in_its_boundary_area(self):
        square = lambda x, y: [[[x, y], [x + 0.02, y], [x + 0.02, y + 0.02], [x, y + 0.02], [x, y]]]
        write(self.root, "store/bounds.geojson", {"type": "FeatureCollection", "features": [
            {"type": "Feature", "properties": {"MAPNAME": "Fishtown"}, "geometry": {"type": "Polygon", "coordinates": square(-75.14, 39.96)}},
            {"type": "Feature", "properties": {"MAPNAME": "Kensington"}, "geometry": {"type": "Polygon", "coordinates": square(-75.13, 39.98)}}]})
        RootedStore.boundary_path = lambda self, city: str(Path(self.root, "bounds.geojson"))
        try:
            # 5 Oak St lists ZIP 19134 (Kensington); its parcel is in that ZIP but inside the Fishtown polygon
            doc, by = self.build_with(FakeCarto({"5 OAK ST": [opa_row("P5", "5 OAK ST", lat=39.97, lng=-75.13)]}))
        finally:
            del RootedStore.boundary_path
        c = by["https://x/2"]
        self.assertEqual(c["pinFrom"], "parcel")
        self.assertEqual((c["area"]["name"], c["area"]["mapping"]), ("Fishtown", "boundary"))

    def test_a_network_failure_keeps_todays_cards(self):
        logs = []
        doc, by = self.build_with(FakeCarto(fail=True), logs)
        self.assertNotIn("lat", by["https://x/2"])
        self.assertNotIn("pinFrom", by["https://x/2"])
        self.assertEqual(doc["summary"]["withPin"], 3)
        self.assertEqual(by["https://x/2"]["estimate"]["kind"], "ppsf")
        self.assertEqual(by["https://x/1"]["estimate"]["kind"], "model")
        self.assertEqual(len(logs), 1)
        self.assertIn("not looked up", logs[0])
        self.assertFalse(Path(self.root, "store", "_lookups", "philadelphia_address_records.json").exists())


if __name__ == "__main__":
    unittest.main()
