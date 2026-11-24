import unittest

from engine import analytics, area_trends
from engine.schema import SINGLE


def sale(i, date, key, price, sqft=1000):
    return {"id": f"s{i}", "date": date, "month": date[:7], "price": price, "type": SINGLE, "address": f"{i} Main St",
            "zip": "19125", "key": key, "sqft": sqft, "ppsf": price / sqft if sqft else None}


def rows_for(key, month, n, price, start=0):
    return [sale(start + j, f"{month}-{(j % 28) + 1:02d}", key, price + j * 100) for j in range(n)]


class AreaSeriesTests(unittest.TestCase):
    def setUp(self):
        self.rows = []
        for m in analytics.month_range("2024-01", "2026-06"):
            self.rows += rows_for("Busy|", m, 6, 300000, start=len(self.rows))
            self.rows += rows_for("Quiet|", m, 1, 150000, start=len(self.rows))
        self.rows += rows_for("Busy|", "2026-07", 3, 999999, start=len(self.rows))   # a month still filling in
        self.areas = [{"key": "Busy|"}, {"key": "Quiet|"}]
        self.months = analytics.month_range("2024-01", "2026-07")

    def test_series_stop_at_the_last_complete_month_and_roll_three_months(self):
        out = area_trends.area_series(self.rows, self.areas, self.months, "2026-06")
        self.assertEqual(out["months"][-1], "2026-06")
        self.assertEqual(len(out["months"]), 30)
        busy = out["areas"]["Busy|"]
        self.assertEqual(busy["count"][-1], 6)
        self.assertLess(busy["median"][-1], 400000)   # the filling-in month never enters
        self.assertEqual(busy["kpi"]["sales12"], 72)
        self.assertFalse(busy["kpi"]["thin"])
        self.assertEqual(sum(busy["hist"]["counts"]), 72)

    def test_thin_areas_publish_no_medians(self):
        quiet = area_trends.area_series(self.rows, self.areas, self.months, "2026-06")["areas"]["Quiet|"]
        self.assertTrue(quiet["kpi"]["thin"])
        self.assertTrue(all(v is None for v in quiet["median"]))   # 3 sales per 3 months is under ROLL_MIN

    def test_sold_summary_uses_complete_months_and_flags_pace(self):
        rows = list(self.rows)
        rows += rows_for("Busy|", "2026-03", 40, 320000, start=len(rows))   # many more sales this spring
        out = area_trends.sold_summary(rows, self.areas, "2026-06")
        self.assertEqual((out["from"], out["before"]), ("2026-01", ["2025-01", "2025-06"]))
        busy, quiet = out["areas"]
        self.assertEqual((busy["n"], busy["nBefore"]), (76, 36))
        self.assertEqual(busy["pace"], "up")
        self.assertLess(busy["median"], 999999)
        self.assertEqual(busy["latestSale"][:7], "2026-07")     # latest individual sales include the newest records
        self.assertEqual(len(busy["latest"]), area_trends.LATEST_N)
        self.assertIsNone(quiet["median"])                       # 6 sales: too few
        self.assertIsNone(quiet["medianYoY"])


if __name__ == "__main__":
    unittest.main()
