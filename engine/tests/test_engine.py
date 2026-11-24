import datetime as dt
import json
import math
import os
import random
import tempfile
import unittest

from engine import analytics, stats
from engine.geo import Locator
from engine.schema import CONDO, COOP, MULTI, SINGLE, Normalizer, title
from engine.sources import Context, chicago, nyc, philadelphia
from engine.store import Store
from collections import Counter

TODAY = dt.date(2026, 11, 23)

try:   # optional: only the Parquet export needs it
    import duckdb  # noqa: F401
    HAS_DUCKDB = True
except ImportError:
    HAS_DUCKDB = False


def sale(norm, **over):
    base = dict(id="a1", date="2026-05-04", price=250000, type=SINGLE, address="1 Main St", zip="19147")
    base.update(over)
    return norm.sale(**base)


class NormalizerTests(unittest.TestCase):
    def test_keeps_a_valid_sale(self):
        norm = Normalizer(TODAY)
        row = sale(norm, sqft="1,200", lat="39.94", lon="-75.15")
        self.assertEqual((row["price"], row["sqft"], row["lat"]), (250000, 1200, 39.94))
        self.assertEqual(norm.rejected, {})

    def test_rejects_nominal_future_and_non_residential(self):
        norm = Normalizer(TODAY)
        self.assertIsNone(sale(norm, price=1))
        self.assertIsNone(sale(norm, date="2031-01-01"))
        self.assertIsNone(sale(norm, type=None))
        self.assertEqual(sum(norm.rejected.values()), 3)
        self.assertEqual(norm.seen, 3)

    def test_implausible_details_are_blanked_not_rejected(self):
        row = sale(Normalizer(TODAY), sqft=5, year_built=1200, lat=0, lon=0, zip="00000")
        self.assertEqual((row["sqft"], row["year_built"], row["lat"], row["zip"]), (None, None, None, ""))

    def test_title(self):
        self.assertEqual(title("1838  MOUNTAIN ST"), "1838 Mountain St")
        self.assertEqual(title("272 EAST 3RD STREET"), "272 East 3rd Street")


class SourceTests(unittest.TestCase):
    def test_philadelphia(self):
        norm = Normalizer(TODAY)
        row = philadelphia.normalize(norm, {
            "doc": 54578116, "display_date": "2026-06-02T08:00:00Z", "street_address": "5821 CEDAR AVE", "zip": "19143",
            "price": 210000, "pc": 1, "opa": "032132100", "lat": 39.95, "lon": -75.24, "cat": "SINGLE FAMILY",
            "bdesc": "ROW 2 STY MASONRY", "sqft": 1456, "yb": "1925", "beds": 3, "mv": 162000})
        self.assertEqual((row["type"], row["address"], row["assessed"]), (SINGLE, "5821 Cedar Ave", 162000))
        record = {"parcel_number": "032132100", "doc": "54578116", "sale_date": "2026-06-01T04:00:00Z", "sale_price": 210000,
                  "location": "5821 CEDAR AVE", "zip_code": "19143", "cat": "SINGLE FAMILY"}
        self.assertEqual(row["id"], philadelphia.normalize_record(norm, record)["id"])     # the two city files merge into one sale
        self.assertIsNone(philadelphia.normalize_record(norm, record, shared={"54578116"}))
        self.assertIsNone(philadelphia.normalize(norm, {"cat": None}))
        self.assertIsNone(philadelphia.normalize(norm, {"cat": "SINGLE FAMILY", "pc": 3}))
        self.assertEqual(philadelphia.classify({"cat": "MULTI FAMILY"}), MULTI)
        self.assertEqual(philadelphia.classify({"cat": "SINGLE FAMILY", "bdesc2": "CONDO"}), CONDO)
        self.assertIsNone(philadelphia.normalize_record(norm, {**record, "bdesc": "RES CONDO 3 STY"}))

    def test_nyc(self):
        norm = Normalizer(TODAY)
        raw = {"borough": "3", "neighborhood": "PARK SLOPE", "building_class_category": "02 TWO FAMILY DWELLINGS",
               "block": "1", "lot": "2", "address": "10 5 AVENUE", "zip_code": "11217", "gross_square_feet": "2,400",
               "year_built": "1910", "sale_price": "2100000", "sale_date": "2026-03-09T00:00:00.000"}
        row = nyc.normalize(norm, raw)
        self.assertEqual((row["type"], row["group"], row["area"], row["sqft"]), (MULTI, "Brooklyn", "Park Slope", 2400))
        self.assertEqual(row["id"], nyc.normalize(norm, dict(raw))["id"])     # ids are stable across pulls
        self.assertIsNone(nyc.normalize(norm, {**raw, "building_class_category": "22 STORE BUILDINGS"}))

    def test_chicago(self):
        norm = Normalizer(TODAY)
        lookup = {"p1": {"address": "625 N HAMLIN AVE", "zip": "60624", "area": "HUMBOLDT PARK", "lat": "41.89", "lon": "-87.72", "sqft": "0"}}
        row = chicago.normalize(norm, {"pin": "p1", "class": "299", "sale_date": "2026-02-01T00:00:00.000", "sale_price": "300000", "row_id": "7"}, lookup)
        self.assertEqual((row["type"], row["area"], row["sqft"]), (CONDO, "Humboldt Park", None))
        self.assertIsNone(chicago.normalize(norm, {"pin": "p1", "class": "299", "is_multisale": True}, lookup))
        lookup["p2"] = {"address": "351 N MAY ST P13"}
        self.assertIsNone(chicago.normalize(norm, {"pin": "p2", "class": "299", "sale_date": "2026-02-01", "sale_price": "50000", "row_id": "8"}, lookup))
        self.assertIn("parking space or garage unit, not a home", norm.rejected)


class StoreTests(unittest.TestCase):
    def test_upsert_is_idempotent_and_never_erases(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, norm = Store(tmp), Normalizer(TODAY)
            first = sale(norm, lat=39.9, lon=-75.1)
            self.assertEqual(store.upsert("x", [first])["new"], 1)
            path = store._path("x", "2026-05")
            read = lambda: __import__("pathlib").Path(path).read_bytes()
            before = read()
            self.assertEqual(store.upsert("x", [first])["unchanged"], 1)
            self.assertEqual(read(), before)                                 # byte-identical: no git churn
            counts = store.upsert("x", [sale(norm, price=260000)])           # corrected price, no coordinates this time
            self.assertEqual(counts["updated"], 1)
            row = next(store.iter_rows("x"))
            self.assertEqual((row["price"], row["lat"]), ("260000", "39.9"))


class IntakeTests(unittest.TestCase):
    def test_sample_keeps_real_verdicts_newest_first_and_bounded(self):
        norm = Normalizer(TODAY, sample=3)
        sale(norm, id="k1", date="2026-05-01")
        sale(norm, id="r1", date="2026-05-03", price=1)                         # rejected inside sale()
        norm.peek(date="2026-05-02", price=300000, address="9 Elm St", kind="Commercial")
        norm.reject("not a home")                                                # rejected by an adapter, after peek
        norm.reject("no row")                                                    # no peek: counted, not sampled
        sale(norm, id="old", date="2026-01-01")                                  # falls out: only the newest 3 are kept
        sale(norm, id="bad", date="2099-01-01")                                  # invalid date: never sampled
        got = norm.sample()
        self.assertEqual([r["date"] for r in got], ["2026-05-03", "2026-05-02", "2026-05-01"])
        self.assertEqual([(r["verdict"], r["reason"]) for r in got],
                         [("reject", "nominal price (under $10,000)"), ("reject", "not a home"), ("keep", None)])
        self.assertEqual(got[1]["address"], "9 Elm St")
        self.assertEqual(got[2]["kind"], SINGLE)
        self.assertEqual(set(got[0]), {"date", "price", "address", "kind", "verdict", "reason"})   # no party names
        self.assertEqual(norm.seen, 6)

    def test_sample_is_carried_forward_and_published_without_churn(self):
        from engine import intake
        with tempfile.TemporaryDirectory() as tmp:
            store, out = Store(tmp), os.path.join(tmp, "out")
            norm = Normalizer(TODAY)
            store.upsert("x", [sale(norm, id="s1", date="2026-05-04")])
            # no sample yet: the newest stored sales stand in, labelled as stored
            fallback = intake.publish(store, "x", out)
            self.assertEqual((fallback["kind"], fallback["records"][0]["date"]), ("stored", "2026-05-04"))
            samples = [{"source": "Deeds", "readAt": "2026-11-23T09:17:00+00:00", "records": norm.sample()}]
            intake.remember(store, "x", samples, "run1")
            first = intake.publish(store, "x", out)
            self.assertEqual((first["kind"], first["runId"], first["records"][0]["source"]), ("read", "run1", "Deeds"))
            path = os.path.join(out, "x", "intake.json")
            mtime = os.stat(path).st_mtime_ns
            # a later run that read nothing (sources unchanged) keeps the last sample and its read time
            self.assertIsNone(intake.remember(store, "x", [], "run2"))
            again = intake.publish(store, "x", out)
            self.assertEqual((again["runId"], again["readAt"]), ("run1", "2026-11-23T09:17:00+00:00"))
            self.assertEqual(os.stat(path).st_mtime_ns, mtime)                      # unchanged content: file not rewritten

    def test_merge_takes_the_newest_across_sources(self):
        from engine import intake
        a = {"source": "A", "records": [{"date": "2026-05-03"}, {"date": "2026-05-01"}]}
        b = {"source": "B", "records": [{"date": "2026-05-02"}]}
        got = intake.merge([a, b], size=2)
        self.assertEqual([(r["date"], r["source"]) for r in got], [("2026-05-03", "A"), ("2026-05-02", "B")])


class AnalyticsTests(unittest.TestCase):
    def test_statistics(self):
        self.assertEqual(analytics.median([3, 1, 2]), 2)
        self.assertEqual(analytics.quantile([1, 2, 3, 4], 0.5), 2.5)
        self.assertEqual(analytics.pct_change(110, 100), 10.0)
        self.assertIsNone(analytics.pct_change(110, None))
        self.assertEqual(analytics.shift("2026-01", -2), "2025-11")
        self.assertEqual(analytics.month_range("2025-11", "2026-02"), ["2025-11", "2025-12", "2026-01", "2026-02"])

    def test_thin_recent_months_are_left_out(self):
        months = analytics.month_range("2025-01", "2026-04")
        counts = [100] * 14 + [70, 5]
        self.assertEqual(months[analytics.last_complete_month(months, counts)], "2026-02")

    def test_by_month_keeps_the_filling_in_tail(self):
        months = analytics.month_range("2024-01", "2026-04")
        counts = list(range(len(months)))
        out = analytics.sales_by_month(months, counts)
        self.assertEqual(len(out), 24)
        self.assertEqual(out[-1], {"month": "2026-04", "count": counts[-1]})
        self.assertEqual(out[0]["month"], "2024-05")
        self.assertEqual(analytics.sales_by_month(months[:3], counts[:3]), [{"month": m, "count": c} for m, c in zip(months[:3], counts[:3])])


class QueryFileTests(unittest.TestCase):
    def rows(self):
        def r(i, date, key, **over):
            base = {"id": f"s{i}", "date": date, "month": date[:7], "price": 200000 + i, "type": SINGLE, "address": f"{i} Main St",
                    "zip": "11104", "key": key, "sqft": 1000, "ppsf": 200.0, "yb": 1925, "beds": None, "lat": 40.74, "lon": -73.92,
                    "assessed": None, "parcel": f"p{i}"}
            return {**base, **over}
        return [r(3, "2026-06-02", "Sunnyside|Staten Island"), r(1, "2020-01-05", "Sunnyside|Queens", sqft=None, lat=None, lon=None),
                r(2, "2026-06-02", "Astoria|Queens"), r(4, "2024-03-01", "")]

    def test_every_sale_is_written_deterministically_with_unique_area_names(self):
        import csv
        import gzip
        cfg = {"area_by": "area", "area_names": {}}
        areas = [{"key": "Astoria|Queens", "name": "Astoria", "group": "Queens", "sales12": 40, "median12": 700000, "ppsf12": 650,
                  "topType": SINGLE, "lat": 40.76, "lon": -73.92}]
        meta = {"city": "nyc", "name": "New York City", "through": "2026-05", "latestSale": "2026-06-02"}
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "nyc"))
            analytics.write_query_files(d, "nyc", cfg, self.rows(), areas, meta)
            first = open(os.path.join(d, "nyc", "sales.csv.gz"), "rb").read()
            analytics.write_query_files(d, "nyc", cfg, list(reversed(self.rows())), areas, meta)
            self.assertEqual(open(os.path.join(d, "nyc", "sales.csv.gz"), "rb").read(), first)   # same rows, same bytes
            with gzip.open(os.path.join(d, "nyc", "sales.csv.gz"), "rt", newline="") as f:
                got = list(csv.DictReader(f))
            self.assertEqual(list(got[0]), analytics.QUERY_COLUMNS)
            self.assertEqual([g["date"] for g in got], ["2020-01-05", "2024-03-01", "2026-06-02", "2026-06-02"])
            self.assertEqual(got[0]["area"], "Sunnyside, Queens")         # two boroughs share the name
            self.assertEqual(got[2]["area"], "Astoria")
            self.assertEqual(got[3]["area"], "Sunnyside, Staten Island")
            self.assertEqual((got[1]["area"], got[0]["sqft"], got[0]["lat"]), ("", "", ""))   # unknowns are empty, read as NULL
            with gzip.open(os.path.join(d, "nyc", "sales-recent.csv.gz"), "rt", newline="") as f:
                self.assertEqual([g["date"] for g in csv.DictReader(f)], ["2024-03-01", "2026-06-02", "2026-06-02"])
            with gzip.open(os.path.join(d, "nyc", "areas.csv.gz"), "rt", newline="") as f:
                area = next(csv.DictReader(f))
            self.assertEqual((area["area"], area["area_group"], area["median_12m"], area["rent"]), ("Astoria", "Queens", "700000", ""))
            info = json.load(open(os.path.join(d, "nyc", "query.json")))
            self.assertEqual((info["full"]["rows"], info["recent"]["rows"], info["recent"]["from"]), (4, 3, "2023-06-02"))
            if not HAS_DUCKDB:   # without the optional package there is no Parquet file and the manifest says so
                self.assertIsNone(info["parquet"])
                self.assertFalse(os.path.exists(os.path.join(d, "nyc", "sales.parquet")))

    @unittest.skipUnless(HAS_DUCKDB, "the optional duckdb package is not installed")
    def test_parquet_export_is_typed_sorted_and_repeatable(self):
        import duckdb
        cfg = {"area_by": "area", "area_names": {}}
        meta = {"city": "nyc", "name": "New York City", "through": "2026-05", "latestSale": "2026-06-02"}
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "nyc"))
            path = os.path.join(d, "nyc", "sales.parquet")
            analytics.write_query_files(d, "nyc", cfg, self.rows(), [], meta)
            first = open(path, "rb").read()
            analytics.write_query_files(d, "nyc", cfg, self.rows(), [], meta)
            self.assertEqual(open(path, "rb").read(), first)
            self.assertEqual(json.load(open(os.path.join(d, "nyc", "query.json")))["parquet"]["rows"], 4)
            con = duckdb.connect()
            got = con.execute(f"SELECT area, date, price, sqft FROM read_parquet('{path}')").fetchall()
            types = dict(con.execute(f"SELECT column_name, column_type FROM (DESCRIBE SELECT * FROM read_parquet('{path}'))").fetchall())
            con.close()
        self.assertEqual([g[0] for g in got], ["Astoria", "Sunnyside, Queens", "Sunnyside, Staten Island", None])   # area, then date
        self.assertEqual((str(got[0][1]), got[0][2], got[1][3]), ("2026-06-02", 200002, None))
        self.assertEqual({c: types[c] for c in ("date", "price", "zip", "lat")}, {"date": "DATE", "price": "INTEGER", "zip": "VARCHAR", "lat": "DOUBLE"})


class CompletenessTests(unittest.TestCase):
    def test_month_is_compared_with_the_same_month_a_year_earlier(self):
        months = analytics.month_range("2024-01", "2026-06")
        season = [60, 70, 90, 100, 110, 110, 105, 100, 95, 90, 70, 80]
        counts = season * 2 + season[:4] + [80, 40]       # 2026-05 at 73% of May 2025, though above 75% of the median
        self.assertGreaterEqual(counts[-2], 0.75 * analytics.median(counts[-14:-2]))
        self.assertEqual(months[analytics.last_complete_month(months, counts)], "2026-04")
        counts = season * 2 + season[:1]                  # a January low that matches last January is complete
        self.assertEqual(analytics.last_complete_month(months[:25], counts), 24)

    def test_falls_back_without_a_year_earlier_month(self):
        months = analytics.month_range("2025-01", "2026-04")
        counts = [0] * 4 + [100] * 10 + [80, 50]          # no sales in early 2025, so nothing a year earlier to compare with
        self.assertEqual(months[analytics.last_complete_month(months, counts)], "2026-03")
        self.assertEqual(analytics.last_complete_month(["2026-01"], [3]), 0)

    def test_walks_back_at_most_six_months(self):
        months = analytics.month_range("2024-01", "2026-12")
        counts = [100] * 24 + [10] * 12
        self.assertEqual(months[analytics.last_complete_month(months, counts)], "2026-06")


class AcrisTests(unittest.TestCase):
    def master(self, doc="2026101500001001", date="2026-10-14T00:00:00.000", amt="850000"):
        return {"document_id": doc, "doc_type": "DEED", "document_date": date, "document_amt": amt}

    def legal(self, **over):
        base = {"document_id": "2026101500001001", "borough": "3", "block": "01097", "lot": "1017", "property_type": "SC",
                "partial_lot": "E", "easement": "N", "air_rights": "N", "subterranean_rights": "N",
                "street_number": "634", "street_name": "11TH STREET", "unit": "17"}
        base.update(over)
        return base

    def lookups(self):
        known = {"3-1100-20": {"date": "2021-05-01", "type": MULTI, "address": "12 Prospect Ave", "zip": "11215",
                               "area": "Park Slope", "group": "Brooklyn", "sqft": "2400", "year_built": "1910",
                               "lat": "40.66", "lon": "-73.98"},
                 "1-500-10": {"date": "2020-01-01", "type": COOP, "address": "1 Fifth Ave", "zip": "10003", "area": "Village",
                              "group": "Manhattan", "sqft": "", "year_built": "", "lat": "", "lon": ""}}
        pluto = {"4-8786-42": {"bldgclass": "A1", "address": "84-74 257 STREET", "zipcode": "11001", "latitude": "40.7346",
                               "longitude": "-73.7105", "bldgarea": "1864", "yearbuilt": "1945"},
                 "4-8786-43": {"bldgclass": "K1", "address": "84-80 257 STREET", "zipcode": "11001"}}
        condos = {"3-1097": [{"bldgclass": "R1", "address": "497 12 STREET", "zipcode": "11215", "latitude": "40.661", "longitude": "-73.981"},
                             {"bldgclass": "R2", "address": "632 11 STREET", "zipcode": "11215", "latitude": "40.663", "longitude": "-73.980"}]}
        blocks = {"3-1097": Counter({("Park Slope", "Brooklyn"): 9, ("Windsor Terrace", "Brooklyn"): 1})}
        return known, pluto, condos, blocks

    def test_condo_unit_takes_its_building_from_pluto(self):
        norm = Normalizer(TODAY)
        row = nyc.normalize_deed(norm, self.master(), [self.legal()], *self.lookups())
        self.assertEqual((row["id"], row["type"], row["parcel"], row["area"], row["group"]),
                         ("acris-2026101500001001", CONDO, "3-1097-1017", "Park Slope", "Brooklyn"))
        self.assertEqual((row["address"], row["zip"], row["lat"], row["date"]), ("634 11th Street, 17", "11215", 40.663, "2026-10-14"))

    def test_house_from_an_earlier_sale_or_pluto(self):
        norm = Normalizer(TODAY)
        row = nyc.normalize_deed(norm, self.master(), [self.legal(block="1100", lot="20", property_type="D2")], *self.lookups())
        self.assertEqual((row["type"], row["address"], row["sqft"], row["area"]), (MULTI, "12 Prospect Ave", 2400, "Park Slope"))
        row = nyc.normalize_deed(norm, self.master(), [self.legal(borough="4", block="8786", lot="42", property_type="D1")], *self.lookups())
        self.assertEqual((row["type"], row["address"], row["zip"], row["sqft"], row["group"]), (SINGLE, "84-74 257 Street", "11001", 1864, "Queens"))

    def test_rejections_are_counted_by_reason(self):
        norm = Normalizer(TODAY)
        look = self.lookups()
        two = [self.legal(), self.legal(lot="1018")]
        self.assertIsNone(nyc.normalize_deed(norm, self.master(), two, *look))
        self.assertIsNone(nyc.normalize_deed(norm, self.master(), [self.legal(property_type="CR")], *look))
        self.assertIsNone(nyc.normalize_deed(norm, self.master(), [self.legal(air_rights="Y")], *look))
        self.assertIsNone(nyc.normalize_deed(norm, self.master(), [self.legal(borough="1", block="500", lot="10", property_type="D3")], *look))
        self.assertIsNone(nyc.normalize_deed(norm, self.master(), [self.legal(block="9999")], *look))   # no condo building on the block
        self.assertIsNone(nyc.normalize_deed(norm, self.master(), [self.legal(borough="4", block="8786", lot="43", property_type="D1")], *look))
        self.assertIsNone(nyc.normalize_deed(norm, self.master(), [], *look))
        self.assertEqual(norm.rejected, {nyc.R_MULTI_LOT: 1, nyc.R_NOT_HOME: 2, nyc.R_PART: 1, nyc.R_COOP_BUILDING: 1,
                                         nyc.R_UNCLASSIFIED: 1, nyc.R_NO_LEGALS: 1})
        self.assertEqual(norm.seen, 7)

    def test_class_types_follow_finance_categories(self):
        self.assertEqual([nyc.class_type(c) for c in ("A5", "S1", "B2", "S2", "C0", "R4", "R9", "D4", "C1", "RG", "")],
                         [SINGLE, SINGLE, MULTI, MULTI, MULTI, CONDO, COOP, COOP, None, None, None])

    def test_prepare_rows_hands_over_and_drops_multi_lot_deeds(self):
        def r(id, date, parcel, price=500000):
            return {"id": id, "date": date, "parcel": parcel, "price": price}
        rows = [r("f1", "2026-09-30", "3-10-1"), r("f2", "2026-07-31", "4-5040-1001", 11992650),
                r("f3", "2026-07-31", "4-5040-1002", 11992650), r("f4", "2026-07-31", "4-5040-1003", 600000),
                r("acris-1", "2026-09-30", "3-10-1"),           # Finance has reached this day: its row is the record
                r("acris-2", "2026-10-02", "3-10-2"),
                r("acris-3", "2026-10-02", "3-10-3"), r("acris-4", "2026-10-02", "3-10-4")]   # separate single-lot deeds stay
        kept = [x["id"] for x in nyc.prepare_rows(rows, log=lambda m: None)]
        self.assertEqual(kept, ["f1", "f4", "acris-2", "acris-3", "acris-4"])


class FakeSocrata:
    """Answers the engine's Socrata queries from in-memory tables."""

    def __init__(self, tables, stamps):
        self.tables, self.stamps = tables, stamps
        self.stats = {"requests": 0, "retries": 0, "bytes": 0}

    def get_json(self, url, params):
        self.stats["requests"] += 1
        if "/api/views/" in url:
            return {"rowsUpdatedAt": self.stamps[url.rsplit("/", 1)[1][:-5]]}
        q = params["$query"]
        if " offset 0" not in q:
            return []
        table = self.tables[url.rsplit("/", 1)[1][:-5]]
        if "document_id in" in q:
            wanted = set(q.split("document_id in (")[1].split(")")[0].replace("'", "").split(","))
            return [row for row in table if row["document_id"] in wanted]
        if "document_date > '" in q:
            after = q.split("document_date > '")[1].split("'")[0]
            return [row for row in table if row["document_date"] > after]
        return list(table)


class AcrisHandOverTests(unittest.TestCase):
    def test_deeds_fill_the_tail_and_leave_when_finance_arrives(self):
        legal = {"borough": "3", "block": "1100", "lot": "20", "property_type": "D2", "partial_lot": "E", "easement": "N",
                 "air_rights": "N", "subterranean_rights": "N", "street_number": "12", "street_name": "PROSPECT AVENUE"}
        masters = [{"document_id": d, "doc_type": "DEED", "document_date": f"{date}T00:00:00.000", "document_amt": "900000"}
                   for d, date in (("D1", "2026-10-05"), ("D2", "2026-10-20"))]
        tables = {nyc.MASTER: masters, nyc.LEGALS: [{**legal, "document_id": "D1"}, {**legal, "lot": "21", "document_id": "D2"}],
                  nyc.PLUTO: []}
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(tmp)
            finance = sale(Normalizer(TODAY), id="fin-1", date="2021-05-01", type=MULTI, parcel="3-1100-20", area="Park Slope", group="Brooklyn")
            stale = sale(Normalizer(TODAY), id="acris-OLD", date="2026-09-20", type=MULTI, parcel="3-1100-22")
            gone = sale(Normalizer(TODAY), id="acris-WITHDRAWN", date="2026-10-03", type=MULTI, parcel="3-1100-23")
            store.upsert("nyc", [finance, stale, gone])
            state = {}

            def run(reach, stamp):
                client = FakeSocrata(tables, {nyc.MASTER: stamp})
                ctx = Context(client=client, store=store, state=state, since="2026-03-01", full=False, today=TODAY, log=lambda m: None)
                nyc.harvest_deeds(ctx, reach)
                return ctx.reports[-1]

            report = run("2026-09-30", 1)
            ids = {r["id"] for r in store.iter_rows("nyc")}
            self.assertEqual(ids, {"fin-1", "acris-D1"})              # D2's lot is unknown to Finance and PLUTO; old deeds retired
            self.assertEqual((report["kept"], report["removed"], report["rejected"]), (1, 2, {nyc.R_UNCLASSIFIED: 1}))
            self.assertEqual(run("2026-09-30", 1)["skipped"], "source unchanged since last pull")
            # Finance publishes October: its row for the same sale has another id, so the deed must go
            store.upsert("nyc", [sale(Normalizer(TODAY), id="fin-2", date="2026-10-05", price=900000, type=MULTI, parcel="3-1100-20")])
            loaded = analytics.load(store, "nyc")
            self.assertEqual(sorted(r["id"] for r in loaded), ["fin-1", "fin-2"])    # analytics drop it at once
            run("2026-10-31", 2)
            self.assertEqual({r["id"] for r in store.iter_rows("nyc")}, {"fin-1", "fin-2"})   # and the next harvest removes it


class StatsTests(unittest.TestCase):
    def test_poisson_two_count_test(self):
        self.assertAlmostEqual(stats.poisson_two_count_p(50, 50), 1.0, places=6)
        self.assertLess(stats.poisson_two_count_p(80, 40), 0.01)
        self.assertGreater(stats.poisson_two_count_p(6, 2), 0.05)       # tripled, but on eight events
        self.assertAlmostEqual(stats.poisson_two_count_p(3, 0), 0.25)   # 2 * (1/2)^3
        # the large-total approximation agrees with the exact test where both are cheap
        exact = stats.poisson_two_count_p(530, 470)
        stats.EXACT_LIMIT, saved = 10, stats.EXACT_LIMIT
        try:
            approx = stats.poisson_two_count_p(530, 470)
        finally:
            stats.EXACT_LIMIT = saved
        self.assertAlmostEqual(exact, approx, delta=0.005)

    def test_count_trend(self):
        self.assertEqual(stats.count_trend(80, 40), ("up", 100.0))
        self.assertEqual(stats.count_trend(40, 80), ("down", -50.0))
        self.assertEqual(stats.count_trend(52, 48), ("flat", None))
        self.assertEqual(stats.count_trend(14, 4), ("flat", None))     # total under 20

    def test_empirical_bayes_shrinkage(self):
        counts, pops = [100, 300, 0, 50], [10000, 20000, 500, 5000]
        est, m, s2, w = stats.eb_rates(counts, pops)
        self.assertAlmostEqual(m, 450 / 35500)
        raw = [o / p for o, p in zip(counts, pops)]
        moved = [abs(e - r) / max(abs(r - m), 1e-12) for e, r in zip(est, raw)]
        self.assertEqual(max(range(4), key=lambda i: moved[i]), 2)       # the tiny area moves most of the way
        self.assertLess(w[2], min(w[0], w[1], w[3]))
        for e, r in zip(est, raw):                                       # every rate ends between raw and the city rate
            self.assertTrue(min(r, m) - 1e-12 <= e <= max(r, m) + 1e-12)
        # identical raw rates: no between-area variance, so everything equals the city rate
        est, m, s2, _ = stats.eb_rates([10, 20, 40], [1000, 2000, 4000])
        self.assertEqual(s2, 0.0)
        for e in est:
            self.assertAlmostEqual(e, 0.01)

    def test_median_interval(self):
        # n = 10 by hand: P(X <= 1) = 11/1024, coverage 0.979; P(X <= 2) = 56/1024, coverage 0.891. So ranks 2 and 9.
        self.assertEqual(stats.median_ci_ranks(10), (2, 9))
        self.assertEqual(stats.median_ci(list(range(1, 11))), (2, 9))
        self.assertIsNone(stats.median_ci_ranks(5))                      # 1 - 2/32 = 0.9375: no 95% interval yet
        self.assertEqual(stats.median_ci_ranks(6), (1, 6))
        widths = []
        for n in (10, 40, 200, 1000, 4000):
            values = sorted(i / n for i in range(n))
            lo, hi = stats.median_ci(values)
            self.assertTrue(lo <= analytics.quantile(values, 0.5) <= hi)
            widths.append(hi - lo)
        self.assertEqual(widths, sorted(widths, reverse=True))           # narrows as n grows
        a, b = stats.median_ci_ranks(1000)
        stats.EXACT_LIMIT, saved = 10, stats.EXACT_LIMIT
        try:
            c, d = stats.median_ci_ranks(1000)
        finally:
            stats.EXACT_LIMIT = saved
        self.assertLessEqual(abs(a - c), 1)

    def test_solver(self):
        self.assertEqual([round(v, 9) for v in stats.solve([[2, 1], [1, 3]], [3, 5])], [0.8, 1.4])
        self.assertIsNone(stats.solve([[1, 2], [2, 4]], [1, 2]))

    def test_repeat_sales_recovers_steady_growth(self):
        g = math.log(1.05)
        pairs = [(t1, t2, (t2 - t1) * g) for t1 in range(7) for t2 in range(t1 + 1, 8) for _ in range(3)]
        levels, half = stats.repeat_sales_index(pairs, 8)
        for t in range(8):
            self.assertAlmostEqual(levels[t], 1.05 ** t, places=9)
        self.assertEqual(sum(half), 2 * len(pairs))

    def test_repeat_sales_with_noise(self):
        rng = random.Random(7)
        truth = [0.0, 0.08, 0.15, 0.10, 0.18, 0.25]
        pairs = []
        for _ in range(4000):
            t1 = rng.randrange(5)
            t2 = rng.randrange(t1 + 1, 6)
            pairs.append((t1, t2, truth[t2] - truth[t1] + rng.gauss(0, 0.15)))
        levels, _ = stats.repeat_sales_index(pairs, 6)
        for t in range(6):
            self.assertAlmostEqual(math.log(levels[t]), truth[t], delta=0.02)
        # a period nobody sold in is left unestimated rather than guessed
        levels, half = stats.repeat_sales_index([(0, 2, 0.1), (0, 2, 0.12), (2, 3, 0.05)], 4)
        self.assertIsNone(levels[1])
        self.assertEqual(half[1], 0)
        self.assertAlmostEqual(levels[3], math.exp(0.16))

    def test_repeat_pairs_filters(self):
        def row(parcel, month, price, address="1 Main St", type_=SINGLE, key="A"):
            return {"parcel": parcel, "month": month, "date": month + "-15", "price": price, "type": type_, "key": key, "address": address}
        rows = [
            row("ok", "2020-03", 200000), row("ok", "2023-03", 230000),            # kept
            row("flip", "2024-01", 100000), row("flip", "2024-08", 180000),        # under a year apart
            row("jump", "2020-01", 100000), row("jump", "2022-01", 300000),        # +73% a year
            row("units", "2020-01", 300000, "5 Elm St #1"), row("units", "2023-01", 320000, "5 Elm St #2"),
            row("coop", "2020-01", 300000, type_=COOP), row("coop", "2023-01", 320000, type_=COOP),
            row("dup", "2020-05", 250000), row("dup", "2020-05", 250000), row("dup", "2024-05", 270000),
        ]
        pairs, periods = analytics.repeat_pairs(rows, "2026-06")
        self.assertEqual(periods, 7)                                         # 2020-01 .. 2026-06: six years and six months
        self.assertEqual(sorted(round(p[2], 4) for p in pairs), sorted([round(math.log(1.15), 4), round(math.log(1.08), 4)]))


class LocatorTests(unittest.TestCase):
    def test_finds_areas_holes_and_multipolygons(self):
        def ring(x0, y0, x1, y1):
            return [[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]

        data = {"type": "FeatureCollection", "features": [
            {"type": "Feature", "properties": {"NAME": "Square"},
             "geometry": {"type": "Polygon", "coordinates": [ring(0, 0, 10, 10), ring(4, 4, 6, 6)]}},
            {"type": "Feature", "properties": {"NAME": "Islands"},
             "geometry": {"type": "MultiPolygon", "coordinates": [[ring(20, 20, 22, 22)], [ring(30, 30, 32, 32)]]}},
        ]}
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "areas.geojson")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f)
            loc = Locator(path, "NAME")
        # find() takes latitude first; GeoJSON stores [lon, lat]
        self.assertEqual(loc.find(2, 3), "Square")
        self.assertIsNone(loc.find(5, 5))
        self.assertEqual(loc.find(21, 21), "Islands")
        self.assertEqual(loc.find(31, 31), "Islands")
        self.assertIsNone(loc.find(50, 50))


class ListingsTests(unittest.TestCase):
    CSV = ("RegionID,SizeRank,RegionName,RegionType,StateName,State,City,Metro,CountyName,"
           + ",".join(f"20{y}-{m:02d}-28" for y in (24, 25) for m in range(1, 13)) + "\n"
           # 19104: Philadelphia, a value every month; last month (2025-12) empty for the whole file except 19104
           + "1,1,19104,zip,PA,PA,Philadelphia,x,Philadelphia County," + ",".join(str(100 + i) for i in range(24)) + "\n"
           + '2,2,19103,zip,PA,PA,Philadelphia,"Philadelphia-Camden, PA-NJ",Philadelphia County,'
           + ",".join(str(50 + i) for i in range(23)) + ",\n"
           + "3,3,8002,zip,NJ,NJ,Cherry Hill,x,Camden County," + ",".join("1" for _ in range(24)) + "\n"
           + "4,4,19001,zip,PA,PA,Abington,x,Montgomery County," + ",".join("1" for _ in range(24)) + "\n")

    def test_parse_keeps_wanted_counties_and_latest_months(self):
        from engine import context
        labels, rows = context.parse_zip_months(self.CSV, {"PA": {"Philadelphia County"}}, months=13)
        self.assertEqual(set(rows), {"19104", "19103"})        # other states and counties skipped
        self.assertEqual(labels[0], "2024-12")
        self.assertEqual(labels[-1], "2025-12")
        self.assertEqual(rows["19104"][2][-1], 123)
        self.assertEqual(rows["19104"][2][0], 111)             # 12 months earlier
        self.assertIsNone(rows["19103"][2][-1])                # quoted commas in Metro do not shift columns

    def test_listing_rows_latest_and_year_ago(self):
        from engine import context
        files = {"active": context.parse_zip_months(self.CSV, {"PA": {"Philadelphia County"}}),
                 "cut": context.parse_zip_months(self.CSV.replace(",100,", ",0.1,"), {"PA": {"Philadelphia County"}})}
        month, rows = context.listing_rows(files, "PA", ["Philadelphia County"])
        self.assertEqual(month, "2025-12")
        self.assertEqual((rows["19104"]["active"], rows["19104"]["active_prev"]), (123, 111))
        self.assertEqual(rows["19103"]["active"], "")
        self.assertEqual(len(rows["19104"]["active_s"].split(";")), 13)

    def lookup(self):
        def rec(active, prev, price, price_prev, cut, cut_prev):
            return {"month": "2026-10", "active": str(active), "active_prev": str(prev), "new": "", "new_prev": "",
                    "price": str(price), "price_prev": str(price_prev), "cut": str(cut), "cut_prev": str(cut_prev)}
        return {"A": rec(100, 80, 300000, 250000, 0.2, 0.1), "B": rec(50, 50, 100000, 100000, 0.4, 0.4)}

    def test_counts_apportioned_by_share_of_zip_sales(self):
        zips = {"A": 30, "B": 10}                 # this area's sales
        city = {"A": 60, "B": 10}                 # the whole city's sales in the same ZIPs
        f = analytics.listing_figures(zips, self.lookup(), city)
        self.assertEqual(f["active"], round(100 * 30 / 60 + 50 * 10 / 10))           # 50 + 50
        self.assertEqual(f["activeYoY"], analytics.pct_change(100, 90))               # (50+50) vs (40+50)
        self.assertEqual(f["listPrice"], round((300000 * 30 + 100000 * 10) / 40))     # sales-weighted mean
        self.assertEqual(f["priceCutShare"], round((0.2 * 30 + 0.4 * 10) / 40 * 100, 1))
        self.assertEqual(f["priceCutShareYoY"], round(((0.2 * 30 + 0.4 * 10) - (0.1 * 30 + 0.4 * 10)) / 40 * 100, 1))   # points
        self.assertIsNone(f["newListings"])                                           # no ZIP has it
        self.assertEqual(f["coverage"], 100)

    def test_whole_city_counts_each_zip_in_full(self):
        f = analytics.listing_figures({"A": 60, "B": 10}, self.lookup())
        self.assertEqual(f["active"], 150)

    def test_low_coverage_is_left_out(self):
        lk = self.lookup()
        self.assertIsNone(analytics.listing_figures({"A": 5, "Z": 10}, lk, {"A": 5, "Z": 10}))   # 33% covered
        f = analytics.listing_figures({"A": 7, "Z": 3}, lk, {"A": 7, "Z": 3})                    # 70% covered
        self.assertEqual(f["coverage"], 70)
        self.assertEqual(f["active"], 100)
        self.assertIsNone(analytics.listing_figures({}, lk))
        self.assertIsNone(analytics.listing_figures({"A": 1}, {}))


if __name__ == "__main__":
    unittest.main()
