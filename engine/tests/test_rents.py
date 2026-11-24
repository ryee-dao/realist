"""Rent sources in tandem: Census Summary File / API parsing, area aggregation, reliability, HUD and the blend."""
import datetime as dt
import os
import tempfile
import unittest
from unittest import mock

from engine import context, rents
from engine.http import FetchError
from engine.sources import Context
from engine.store import Store

SF_64 = ("GEO_ID|B25064_E001|B25064_M001\n0100000US|1413|2\n"
         "1400000US42101000100|1500|150\n1400000US42101000200|-666666666|-222222222\n"
         "1400000US42101000300|3501|-333333333\n1400000US17031010100|1100|90\n")
SF_03 = ("GEO_ID|B25003_E001|B25003_M001|B25003_E002|B25003_M002|B25003_E003|B25003_M003\n"
         "1400000US42101000100|1000|50|400|40|600|60\n1400000US42101000200|300|30|290|30|10|8\n"
         "1400000US42101000300|500|50|100|20|400|45\n1400000US42101000400|0|14|0|14|0|14\n"
         "1400000US17031010100|900|40|300|30|600|50\n")
SF_31 = ("GEO_ID|" + "|".join(f"B25031_{k}00{i}" for i in range(1, 8) for k in ("E", "M")) + "\n"
         "1400000US42101000100|1500|150|1100|200|1300|120|1600|140|2000|300|-666666666|-222222222|-666666666|-222222222\n")


def tract(rent, moe, renters, year=2024, open_=""):
    return {"rent": rent, "rent_moe": moe, "renters": renters, "open": open_, "year": year}


class SummaryFileTest(unittest.TestCase):
    def test_parse_keeps_wanted_tracts_and_codes(self):
        cells = context.parse_summary_file(SF_64, ["42101"])
        self.assertEqual(sorted(cells), ["42101000100", "42101000200", "42101000300"])
        rec = context.acs_tract_record({**cells["42101000100"], "B25003_E003": "600", "B25003_M003": "60"})
        self.assertEqual((rec["rent"], rec["rent_moe"], rec["renters"], rec["open"]), (1500, 150, 600, ""))
        missing = context.acs_tract_record(cells["42101000200"])
        self.assertEqual((missing["rent"], missing["rent_moe"]), ("", ""))
        top = context.acs_tract_record(cells["42101000300"])
        self.assertEqual((top["rent"], top["open"], top["rent_moe"]), (3501, 1, ""))

    def test_bedrooms(self):
        rec = context.acs_tract_record(context.parse_summary_file(SF_31, ["42101"])["42101000100"])
        self.assertEqual((rec["b0"], rec["b1"], rec["b2"], rec["b3"], rec["b4"]), (1100, 1300, 1600, 2000, ""))
        self.assertEqual(rec["b1_moe"], 120)

    def test_api_rows_rekeyed(self):
        rows = [["B25064_001E", "B25064_001M", "state", "county", "tract"], ["1500", "150", "42", "101", "000100"]]
        self.assertEqual(context.parse_api_rows(rows), {"42101000100": {"B25064_E001": "1500", "B25064_M001": "150"}})

    def test_harvest_from_summary_file(self):
        files = {"b25064": SF_64, "b25003": SF_03, "b25031": SF_31}

        class Client:
            stats = {"requests": 0}
            heads = []

            def head(self, url):
                self.heads.append(url)
                if "/2024/" not in url:
                    raise FetchError("404")
                return {"last-modified": "x"}

            def get_bytes(self, url):
                return files[url.rsplit("-", 1)[1][:-4]].encode()

        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"CENSUS_API_KEY": ""}):
            store, state = Store(d), {}
            ctx = Context(client=Client(), store=store, state=state, since="", full=False, today=dt.date(2026, 11, 24))
            report = {"fetched": 0, "kept": 0, "rejected": {}, "skipped": None}
            context.harvest_census_rents(ctx, "philadelphia", report, {})
            held = store.read_lookup("philadelphia_census_rents")
            self.assertEqual(sorted(held), ["42101000100", "42101000200", "42101000300"])   # 400 has no renters
            self.assertEqual(held["42101000100"]["year"], "2024")
            self.assertEqual(state["census_rents_year"], 2024)
            # a second run within 30 days makes no request at all
            again = {"fetched": 0, "kept": 0, "rejected": {}, "skipped": None}
            Client.heads.clear()
            context.harvest_census_rents(ctx, "philadelphia", again, {})
            self.assertIn("checked", again["skipped"])
            self.assertEqual(Client.heads, [])


class AggregationTest(unittest.TestCase):
    def test_shares_follow_block_groups(self):
        groups = {"421010001001": {"lat": "1", "lon": "0", "pop": "300"}, "421010001002": {"lat": "2", "lon": "0", "pop": "100"}}
        shares = rents.tract_shares(groups, lambda p: "A" if p[0] < 1.5 else "B")
        self.assertEqual(shares, {"A": {"42101000100": 0.75}, "B": {"42101000100": 0.25}})

    def test_weighted_by_renters_with_margin(self):
        tracts = {"t1": tract("1000", "100", "300"), "t2": tract("2000", "200", "100")}
        p = rents.paid_rent({"t1": 1.0, "t2": 1.0}, tracts)
        self.assertEqual(p["value"], 1250)                                   # (1000*300 + 2000*100) / 400
        self.assertEqual(p["moe"], round(((0.75 * 100) ** 2 + (0.25 * 200) ** 2) ** 0.5))
        self.assertTrue(p["reliable"])
        self.assertEqual((p["period"], p["renters"]), ("2020–2024", 400))

    def test_unreliable_when_margin_large_or_base_tiny(self):
        wide = rents.paid_rent({"t": 1.0}, {"t": tract("1000", "300", "800")})
        self.assertFalse(wide["reliable"])                                   # 30% margin
        tiny = rents.paid_rent({"t": 0.1}, {"t": tract("1000", "50", "800")})
        self.assertFalse(tiny["reliable"])                                   # 80 renter homes
        thin = rents.paid_rent({"a": 1.0, "b": 1.0}, {"a": tract("1000", "50", "400"), "b": tract("", "", "600")})
        self.assertFalse(thin["reliable"])                                   # 40% of renters have a median
        top = rents.paid_rent({"a": 1.0, "b": 1.0}, {"a": tract("3501", "", "600", open_="1"), "b": tract("1500", "50", "400")})
        self.assertFalse(top["reliable"])                                    # mostly $3,500+

    def test_roll_factor_needs_dollar_year(self):
        zori = {"19103": {"rent": "1800", "annual": "2023:1500;2024:1600"}, "19104": {"rent": "1500", "annual": "2025:1400"}}
        self.assertAlmostEqual(rents.roll_factor({"19103": 10}, zori, 2024), 1800 / 1600)
        self.assertIsNone(rents.roll_factor({"19103": 5, "19104": 5}, zori, 2024))     # half the weight lacks 2024
        self.assertIsNone(rents.roll_factor({"19999": 5}, zori, 2024))

    def test_annual_averages_need_whole_years(self):
        months = [f"2024-{m:02d}-31" for m in range(1, 13)] + ["2025-01-31"]
        self.assertEqual(context.zori_annual(months, [1200.0] * 12 + [1300.0]), "2024:1200")


class BlendTest(unittest.TestCase):
    paid = {"value": 1000, "reliable": True, "year": 2024}

    def test_midpoint_and_range(self):
        b = rents.blend(1400, self.paid, 1.1)
        self.assertEqual((b["rent"], b["low"], b["high"], b["basis"], b["paidToday"]), (1250, 1100, 1400, "blend", 1100))
        self.assertFalse(b["disagree"])                                      # 300 / 1250 = 24%
        self.assertTrue(rents.blend(1600, self.paid, 1.0)["disagree"])

    def test_fallbacks(self):
        self.assertEqual(rents.blend(1400, None, None)["basis"], "asking")
        self.assertEqual(rents.blend(1400, {**self.paid, "reliable": False}, 1.1)["basis"], "asking")
        self.assertEqual(rents.blend(1400, self.paid, None)["basis"], "asking")   # not brought to today: left out
        self.assertEqual(rents.blend(None, self.paid, 1.2)["rent"], 1200)
        self.assertEqual(rents.blend(None, self.paid, None)["basis"], "survey")
        self.assertIsNone(rents.blend(None, {**self.paid, "reliable": False}, None))

    def test_hud_is_a_cross_check(self):
        safmr = {"19103": {"year": "2026", "b1": "1700", "b2": "2040"}, "19104": {"year": "2026", "b1": "1500", "b2": ""}}
        h = rents.hud_rents({"19103": 3, "19104": 1}, safmr)
        self.assertEqual(h, {"year": "2026", "beds": {"1": 1650, "2": 2040}})
        self.assertIsNone(rents.hud_rents({"19999": 3}, safmr))


class HudTest(unittest.TestCase):
    PAYLOAD = {"data": {"metro_name": "Philadelphia", "smallarea_status": "1", "basicdata": [
        {"zip_code": "19103", "Efficiency": 1530, "One-Bedroom": 1700, "Two-Bedroom": 2040, "Three-Bedroom": 2500,
         "Four-Bedroom": 2850, "FMR Percentile": 40},
        {"zip_code": "MSA level", "Efficiency": 1200}], "year": "2027"}}

    def ctx(self, store, client):
        return Context(client=client, store=store, state={}, since="", full=False, today=dt.date(2026, 11, 24))

    def test_parse(self):
        year, zips, metro = context.parse_safmr(self.PAYLOAD)
        self.assertEqual(year, "2027")
        self.assertEqual(zips, {"19103": {"b0": 1530, "b1": 1700, "b2": 2040, "b3": 2500, "b4": 2850}})
        self.assertEqual(metro, {"b0": 1200})
        # New York answers with one metro-wide entry, the year inside it
        nyc = {"data": {"area_name": "New York, NY HUD Metro FMR Area", "basicdata": {"Efficiency": 2593, "Two-Bedroom": 2971, "year": "2027"}}}
        self.assertEqual(context.parse_safmr(nyc), ("2027", {}, {"b0": 2593, "b2": 2971}))

    def test_no_token_skips_without_requests(self):
        client = mock.Mock()
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {context.HUD_TOKEN_ENV: ""}):
            report = {"fetched": 0, "kept": 0, "rejected": {}, "skipped": None}
            context.harvest_safmr(self.ctx(Store(d), client), "philadelphia", report)
        self.assertIn("token", report["skipped"])
        client.get_json.assert_not_called()

    def test_bearer_token_and_401(self):
        seen = []

        class Client:
            stats = {"requests": 0}

            def get_json(self, url, params, headers=None):
                seen.append((url, headers))
                return HudTest.PAYLOAD

        class Refused(Client):
            def get_json(self, url, params, headers=None):
                raise FetchError("www.huduser.gov returned 401: unauthorized")

        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {context.HUD_TOKEN_ENV: "tok"}):
            store = Store(d)
            context.harvest_safmr(self.ctx(store, Client()), "philadelphia", {"fetched": 0, "kept": 0, "rejected": {}, "skipped": None})
            self.assertEqual(seen, [("https://www.huduser.gov/hudapi/public/fmr/data/4210199999", {"Authorization": "Bearer tok"})])
            held = store.read_lookup("philadelphia_safmr")
            self.assertEqual((held["19103"]["b2"], held["19103"]["year"], held["metro"]["b0"]), ("2040", "2027", "1200"))
            # a refusal fails this job only (context.harvest reports it as a warning) and keeps what is held
            ctx = Context(client=Refused(), store=store, state={}, since="", full=True, today=dt.date(2026, 11, 24))
            with self.assertRaises(FetchError):
                context.harvest_safmr(ctx, "philadelphia", {"fetched": 0, "kept": 0, "rejected": {}, "skipped": None})
            self.assertIn("19103", store.read_lookup("philadelphia_safmr"))


if __name__ == "__main__":
    unittest.main()
