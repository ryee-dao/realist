import datetime as dt
import math
import random
import tempfile
import unittest

from engine import characteristics, model
from engine.characteristics import building_form, philadelphia_features
from engine.sources import Context
from engine.store import Store

SPEC = [
    {"name": "Floor area", "kind": "log", "unit": "+10% floor area"},
    {"name": "Condition", "kind": "cat", "ref": "Average", "headline": "Below average",
     "order": ["Rehabbed", "Average", "Below average"]},
]


def synthetic(n=4000, seed=7, missing=0.1):
    """Sales with known effects: elasticity 0.8 on floor area, rehab +20%, below average -25%, area and quarter effects."""
    rnd = random.Random(seed)
    areas = {"A": 0.0, "B": 0.3, "C": -0.2, "D": 0.1}
    quarters = {"2023 Q1": 0.0, "2023 Q2": 0.02, "2023 Q3": 0.04, "2023 Q4": 0.05,
                "2024 Q1": 0.06, "2024 Q2": 0.08, "2024 Q3": 0.09, "2024 Q4": 0.1,
                "2025 Q1": 0.12, "2025 Q2": 0.14}
    cond = {"Rehabbed": math.log(1.2), "Average": 0.0, "Below average": math.log(0.75)}
    rows = []
    for i in range(n):
        q = rnd.choice(list(quarters))
        a = rnd.choice(list(areas))
        c = rnd.choice(list(cond))
        sqft = rnd.uniform(700, 3000)
        y = 11.0 + 0.8 * (math.log(sqft) - 7.2) + cond[c] + areas[a] + quarters[q] + rnd.gauss(0, 0.1)
        if i % 97 == 0:
            y -= 2.5            # a few non-market transfers far below value
        month = (int(q[-1]) - 1) * 3 + 1
        rows.append({"id": f"s{i}", "date": f"{q[:4]}-{month:02d}-15", "price": round(math.exp(y)), "y": y,
                     "type": "Single family", "area": a, "period": q, "sqft": round(sqft),
                     "value": round(math.exp(y + rnd.gauss(0, 0.2))),
                     "f": {"Floor area": math.log(sqft), "Condition": None if rnd.random() < missing else c}})
    return rows


class LinearAlgebraTests(unittest.TestCase):
    def test_cholesky_solve_and_inverse(self):
        a = [[4.0, 2.0, 0.6], [2.0, 5.0, 1.0], [0.6, 1.0, 3.0]]
        low = model.cholesky(a)
        x = model.chol_solve(low, [1.0, 2.0, 3.0])
        for i in range(3):
            self.assertAlmostEqual(sum(a[i][j] * x[j] for j in range(3)), [1.0, 2.0, 3.0][i])
        h = model.chol_inverse(low)
        for i in range(3):
            for j in range(3):
                self.assertAlmostEqual(sum(a[i][k] * h[k][j] for k in range(3)), 1.0 if i == j else 0.0)
        self.assertIsNone(model.cholesky([[1.0, 2.0], [2.0, 1.0]]))

    def test_sparse_accumulation_matches_dense(self):
        rows = [[(0, 1.0), (2, 0.5)], [(0, 1.0), (1, 1.0), (2, -1.0)]]
        a = model.accumulate(rows, 3)
        self.assertEqual(a, [[2.0, 1.0, -0.5], [1.0, 1.0, -1.0], [-0.5, -1.0, 1.25]])


class FitTests(unittest.TestCase):
    def setUp(self):
        self.rows = synthetic()
        self.m = model.fit(model.Design(SPEC, self.rows), self.rows)

    def test_recovers_known_effects_and_trims_outliers(self):
        eff = model.effects(self.m)
        cond = {lv["level"]: lv for lv in eff["Condition"]}
        self.assertAlmostEqual(cond["Rehabbed"]["pct"], 20, delta=1.5)
        self.assertAlmostEqual(cond["Below average"]["pct"], -25, delta=1.5)
        self.assertLess(cond["Rehabbed"]["lo"], cond["Rehabbed"]["pct"])
        self.assertGreater(cond["Rehabbed"]["hi"], cond["Rehabbed"]["pct"])
        self.assertTrue(cond["Average"]["ref"])
        self.assertIn(model.NOT_RECORDED, cond)
        size = eff["Floor area"][0]
        self.assertAlmostEqual(size["elasticity"], 0.8, delta=0.03)
        self.assertGreaterEqual(self.m["trimmed"], len(self.rows) // 97)   # the planted transfers are removed
        self.assertLess(self.m["rmse"], 0.12)

    def test_area_premiums_are_centred_and_ordered(self):
        prem, _, _ = model.area_premiums(self.m)
        names = [p["name"] for p in prem]
        self.assertEqual(names[0], "B")
        self.assertEqual(names[-1], "C")
        # B is 0.5 log points above C
        b, c = prem[0]["premium"], prem[-1]["premium"]
        self.assertAlmostEqual(math.log(1 + b / 100) - math.log(1 + c / 100), 0.5, delta=0.03)

    def test_restricted_matches_a_refit_without_the_group(self):
        cols = self.m["design"].groups["Condition"]
        beta = model.restricted(self.m, cols)
        for c in cols:
            self.assertEqual(beta[c], 0.0)
        self.assertGreater(model.sse_of(self.m, beta), self.m["sse"])
        self.assertAlmostEqual(model.sse_of(self.m, self.m["beta"]), self.m["sse"], delta=self.m["sse"] * 1e-6)

    def test_unseen_quarters_take_the_given_level(self):
        train = [r for r in self.rows if r["period"] < "2025"]
        m = model.fit(model.Design(SPEC, train), train, robust=False)
        test = [r for r in self.rows if r["period"] >= "2025"][:50]
        p0 = model.predict(m, test, period_effect=0.0)
        p1 = model.predict(m, test, period_effect=0.1)
        for a, b in zip(p0, p1):
            self.assertAlmostEqual(b - a, 0.1)


class BacktestTests(unittest.TestCase):
    def test_scores(self):
        s = model.score([0.0, math.log(1.05), math.log(0.85), math.log(1.3)])
        self.assertEqual(s["n"], 4)
        self.assertEqual(s["within10"], 50.0)
        self.assertEqual(s["within20"], 75.0)
        aligned, shift = model.align([0.1, 0.2, 0.3, -0.1], ["a", "a", "a", "b"])
        self.assertAlmostEqual(shift["a"], 0.2)
        self.assertAlmostEqual(aligned[3], 0.0)

    def test_backtest_scores_every_method_on_the_same_sales(self):
        rows = synthetic(n=5000)
        train = [r for r in rows if r["date"] <= model.TRAIN_END]
        test = [r for r in rows if r["date"] > model.TRAIN_END]
        m = model.fit(model.Design(SPEC, train), train, robust=False)
        preds = {"model": model.predict(m, test, period_effect=model.forecast_level(m)),
                 "ppsf": model.ppsf_baseline(train, test, "2024-01-01"),
                 "assessorNow": [math.log(r["value"]) for r in test]}
        bt = model.backtest(preds, test, list(range(len(test))))
        by = {x["id"]: x for x in bt["methods"]}
        self.assertLess(by["model"]["aligned"]["mdape"], by["ppsf"]["aligned"]["mdape"])
        self.assertLess(by["model"]["aligned"]["mdape"], by["assessorNow"]["aligned"]["mdape"])
        self.assertLess(by["model"]["forecast"]["bias"], 0)       # the 2024 level carried forward under-predicts 2025
        self.assertEqual(bt["n"], len(test))
        self.assertEqual(sum(b["n"] for b in bt["byBand"]), bt["n"])

    def test_analyse_end_to_end(self):
        rows = synthetic(n=3000)
        rnd = random.Random(3)
        sales = [{"id": r["id"], "date": r["date"], "price": r["price"], "type": r["type"], "key": r["area"],
                  "sqft": r["sqft"], "parcel": r["id"], "lat": 39.95 + rnd.uniform(0, 0.02), "lon": -75.16 + rnd.uniform(0, 0.02)}
                 for r in rows]
        by_id = {r["id"]: r for r in rows}
        lookup = {r["id"]: {} for r in rows}
        lookup.pop("s5")
        history = {r["id"]: {"mv": f"{r['date'][:4]}:{r['value']}"} for r in rows}
        cfg = {"spec": SPEC, "features": lambda s, ch: by_id[s["id"]]["f"], "value": lambda ch: 100000,
               "value_name": "Assessed value", "value_for_year": characteristics.value_for_year}
        out, res = model.analyse(sales, lookup, cfg, history=history, log=lambda *_: None)
        self.assertEqual(out["dropped"], {"no property record for the parcel": 1})
        self.assertEqual({d["name"] for d in out["drivers"]}, {"Floor area", "Condition", "Nearby sales", "Neighbourhood"})
        self.assertTrue(all(d["oos"] is not None for d in out["drivers"]))
        ids = [m["id"] for m in out["backtest"]["methods"]]
        self.assertEqual(ids, ["assessor", "model", "nearby", "adjusted", "ppsf", "assessorNow"])
        self.assertTrue(out["assessorMisses"]["drivers"])
        self.assertEqual(len(res["id"]), len(res["pct"]))
        self.assertTrue(res["id"])
        self.assertTrue(all(i in {r["id"] for r in rows if r["date"] > model.TRAIN_END} for i in res["id"]))


class NearbyTests(unittest.TestCase):
    def test_only_earlier_sales_nearest_first_same_type_preferred(self):
        recs = []
        for i in range(40):
            day = dt.date(2024, 1, 1) + dt.timedelta(days=i)
            recs.append({"date": day.isoformat(), "ord": day.toordinal(), "type": "Single family" if i % 4 else "Multi-family",
                         "lat": 39.95 + i * 0.0003, "lon": -75.16})
        recs.append({"date": recs[-1]["date"], "ord": recs[-1]["ord"], "type": "Single family", "lat": 39.95, "lon": -75.16})
        recs.append({"date": "2024-03-01", "ord": dt.date(2024, 3, 1).toordinal(), "type": "Single family", "lat": None, "lon": None})
        comps = model.nearby_sales(recs, k=5)
        self.assertEqual(comps[0], [])                         # nothing earlier
        for i, c in enumerate(comps[:40]):
            self.assertTrue(all(recs[j]["date"] < recs[i]["date"] for j in c))
        last = comps[40]                                        # same day as record 39: 39 is not a comparable
        self.assertNotIn(39, last)
        self.assertEqual(last, [1, 2, 3, 5, 6])                # nearest single-family sales, multi-family skipped
        self.assertEqual(comps[41], [])                         # no location
        self.assertEqual(model.nearby_feature([[1, 2, 3, 5, 6], [1]], [0.0, 0.1, 0.2, 0.3, 0.0, 0.5, 0.6]), [0.3, None])

    def test_value_for_year(self):
        row = {"mv": "2024:150000;2025:180000;2026:5000"}
        self.assertEqual(characteristics.value_for_year(row, 2025), 180000)
        self.assertIsNone(characteristics.value_for_year(row, 2026))     # nominal values are not values
        self.assertIsNone(characteristics.value_for_year(None, 2025))


class CharacteristicsTests(unittest.TestCase):
    def test_building_form(self):
        self.assertEqual(building_form("ROW PORCH FRONT", ""), "Row")
        self.assertEqual(building_form("TWIN CONVENTIONAL", ""), "Twin")
        self.assertEqual(building_form("COLONIAL", ""), "Detached")
        self.assertEqual(building_form(None, "S/D W/GAR 1 STY FRAME"), "Twin")
        self.assertEqual(building_form(None, None), "")

    def test_philadelphia_features(self):
        sale = {"date": "2024-05-01", "type": "Multi-family", "sqft": 1200, "yb": None, "beds": None}
        ch = {"ic": "5", "ec": "4", "baths": "0", "beds": "3", "stories": "2", "sqft": "", "lot": "1000",
              "garage": "0", "air": "", "bsmt": "A", "grade": "C+", "vw": "I", "fp": "1", "yb": "1925", "form": "Row"}
        f = philadelphia_features(sale, ch)
        self.assertAlmostEqual(f["Floor area"], math.log(1200))
        self.assertEqual(f["Interior condition"], "Below average")
        self.assertEqual(f["Age at sale"], "75-99 yrs")
        self.assertIsNone(f["Bathrooms"])          # 0 means not recorded
        self.assertIsNone(f["Central air"])
        self.assertEqual(f["Garage"], "None")
        self.assertEqual(f["Basement"], "Full, finished")
        self.assertEqual(f["Home type"], "Multi-family")


class FakeCarto:
    def __init__(self, rows):
        self.rows, self.stats, self.queries = rows, {"requests": 0}, []

    def get_json(self, url, params):
        self.stats["requests"] += 1
        self.queries.append(params["q"])
        last = int(params["q"].split("cartodb_id > ")[1].split()[0])
        return {"rows": [r for r in self.rows if r["cid"] > last][:characteristics.PHL_PAGE]}


class HarvestTests(unittest.TestCase):
    def test_keeps_sold_parcels_and_refreshes_weekly(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(tmp)
            store.upsert("philadelphia", [{"id": "doc-1", "date": "2024-01-02", "price": 200000, "type": "Single family",
                                           "parcel": "111"}])
            client = FakeCarto([{"cid": 1, "p": "111", "ic": "4", "ec": 4, "bdesc": "ROW TYPICAL", "mv": 180000},
                                {"cid": 2, "p": "222", "ic": "3"}])
            state = {}
            ctx = Context(client=client, store=store, state=state, since="2019-01-01", full=False, today=dt.date(2026, 1, 1))
            report = {"fetched": 0, "kept": 0, "rejected": {}, "skipped": None}
            characteristics.harvest_philadelphia(ctx, report)
            lookup = store.read_lookup("philadelphia_characteristics")
            self.assertEqual(set(lookup), {"111"})
            self.assertEqual(lookup["111"]["form"], "Row")
            self.assertEqual(lookup["111"]["ec"], "4")
            self.assertEqual(report["kept"], 1)
            self.assertEqual(report["rejected"], {"parcel has no stored sale": 1})
            self.assertNotIn("owner", " ".join(client.queries))
            again = {"fetched": 0, "kept": 0, "rejected": {}, "skipped": None}
            ctx.today = dt.date(2026, 1, 5)
            characteristics.harvest_philadelphia(ctx, again)
            self.assertTrue(again["skipped"])
            ctx.today = dt.date(2026, 1, 9)
            later = {"fetched": 0, "kept": 0, "rejected": {}, "skipped": None}
            characteristics.harvest_philadelphia(ctx, later)
            self.assertIsNone(later["skipped"])
            self.assertEqual(later["unchanged"], 1)


if __name__ == "__main__":
    unittest.main()
