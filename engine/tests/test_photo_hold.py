import json
import os
import shutil
import subprocess
import tempfile
import unittest
import unittest.mock
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path

from engine import listing_analysis as la
from engine.photo_hold import MANIFEST, PhotoHold, Robots, check_published
from engine.tests.test_listing_analysis import FakeBackend, good_tags, ok, png, write_city

NOW = datetime(2026, 11, 24, 12, tzinfo=timezone.utc)
JPEG = b"\xff\xd8\xff\xe0" + b"\0" * 200


class AllowAll:
    def allowed(self, url):
        return True


def item(url="https://a.test/1", n=4, host="img.test", source="Movoto", date="2026-11-23"):
    return {"url": url, "photoUrls": [f"https://{host}/{url[-1]}-{i}.jpg" for i in range(n)], "source": source,
            "date": date, "address": "1 Main St"}


def http_error(code):
    return urllib.error.HTTPError("https://img.test/x", code, "refused", {}, None)


class FetchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.hold = PhotoHold(Path(self.tmp, "hold"))
        self.logs = []

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def fetch(self, items, loader, **kw):
        kw.setdefault("now", NOW)
        return self.hold.fetch("philadelphia", items, loader=loader, robots=kw.pop("robots", AllowAll()),
                               log=self.logs.append, **kw)

    def test_resume_after_partial_download(self):
        calls, failed = [], []

        def flaky(url):
            calls.append(url)
            if url.endswith("-2.jpg") and not failed:
                failed.append(url)
                raise OSError("connection reset")
            return JPEG, "jpg"
        it = item()
        first = self.fetch([it], flaky)
        self.assertEqual((first["photos"], first["ready"], first["failures"]), (3, 0, 1))
        self.assertFalse(self.hold.ready("philadelphia", it["url"], it["photoUrls"]))
        m = self.hold.manifest("philadelphia", it["url"])
        self.assertEqual(m["photos"][it["photoUrls"][2]]["failures"], 1)
        calls.clear()
        second = self.fetch([it], flaky)
        self.assertEqual(calls, [it["photoUrls"][2]])            # only the missing photo
        self.assertEqual((second["photos"], second["ready"]), (1, 1))
        self.assertTrue(self.hold.ready("philadelphia", it["url"], it["photoUrls"]))

    def test_refusal_stops_the_host_and_cools_down(self):
        calls = []

        def refuse(url):
            calls.append(url)
            if "img.test" in url:
                raise http_error(403)
            return JPEG, "jpg"
        a, b = item("https://a.test/1"), item("https://a.test/2", host="other.test")
        rep = self.fetch([a, b], refuse)
        self.assertEqual(rep["stoppedHosts"], ["img.test"])
        self.assertEqual(sum("img.test" in c for c in calls), 1)  # no retry around the refusal
        self.assertEqual(rep["ready"], 1)                          # the other host still served its photos
        hosts = json.loads(Path(self.tmp, "hold", "hosts.json").read_text())
        self.assertEqual(hosts["img.test"]["coolUntil"][:10], "2026-11-25")
        calls.clear()
        self.fetch([a], refuse, now=NOW + timedelta(hours=12))     # still cooling down: not asked again
        self.assertEqual(calls, [])
        self.fetch([a], refuse, now=NOW + timedelta(days=2))       # second refusal: 3 days
        hosts = json.loads(Path(self.tmp, "hold", "hosts.json").read_text())
        self.assertEqual(hosts["img.test"]["coolUntil"][:10], "2026-11-29")
        self.assertIn("HTTP 403", self.logs[0])

    def test_robots_disabled_source_and_budget(self):
        robots = Robots(read=lambda url: "User-agent: *\nDisallow: /\n")
        rep = self.fetch([item()], lambda u: (JPEG, "jpg"), robots=robots)
        self.assertEqual((rep["photos"], rep["failures"]), (0, 4))
        missing = Robots(read=lambda url: "")                      # no robots.txt: allowed
        self.assertTrue(missing.allowed("https://img.test/a.jpg"))

        def broken(url):
            raise OSError("timeout")
        self.assertFalse(Robots(read=broken).allowed("https://img.test/a.jpg"))   # unreadable: not allowed
        rep = self.fetch([item(source="EveryHome")], lambda u: (JPEG, "jpg"))
        self.assertEqual((rep["listings"], rep["skipped"]), (0, 1))
        rep = self.fetch([item("https://a.test/3"), item("https://a.test/4")], lambda u: (JPEG, "jpg"),
                         byte_budget=len(JPEG) * 5)
        self.assertTrue(rep["budgetReached"])
        self.assertEqual(rep["photos"], 5)

    def test_shrunk_on_save_or_original_kept(self):
        it = item(n=1)
        self.fetch([it], lambda u: (b"\x89PNG\r\n\x1a\n" + b"\0" * 999, "png"), shrink=lambda path: JPEG[:50])
        path = self.hold.held("philadelphia", it["url"], it["photoUrls"][0])
        self.assertEqual((path.suffix, path.stat().st_size), (".jpg", 50))
        other = item("https://a.test/9", n=1)
        self.fetch([other], lambda u: (b"\x89PNG\r\n\x1a\n" + b"\0" * 999, "png"))
        self.assertEqual(self.hold.held("philadelphia", other["url"], other["photoUrls"][0]).suffix, ".png")

    def test_expiry_cap_and_takedown(self):
        items = [item(f"https://a.test/{i}", date=d) for i, d in enumerate(["2026-11-23", "2026-10-01", "2026-11-20"])]
        self.fetch(items, lambda u: (JPEG, "jpg"))
        self.fetch([item("https://a.test/7")], lambda u: (JPEG, "jpg"), now=NOW - timedelta(days=15))
        active = {"https://a.test/0": "2026-11-23", "https://a.test/1": "2026-10-01", "https://a.test/7": "2026-11-22"}
        gone = self.hold.expire("philadelphia", active, NOW, log=self.logs.append)
        # 1: over 30 days past its list date; 2: no longer listed; 7: held 15 days untagged
        self.assertEqual(gone, 3)
        self.assertEqual([m["url"] for _, _, m in self.hold.listings()], ["https://a.test/0"])
        # size cap: oldest listing first
        capped = PhotoHold(Path(self.tmp, "capped"))
        for i, when in enumerate([NOW - timedelta(hours=3), NOW - timedelta(hours=1)]):
            capped.fetch("philadelphia", [item(f"https://b.test/{i}")], loader=lambda u: (JPEG, "jpg"),
                         robots=AllowAll(), now=when, log=self.logs.append)
        capped.max_bytes = capped.size() - 1
        self.assertEqual(capped.enforce_cap(log=self.logs.append), 1)
        self.assertEqual([m["url"] for _, _, m in capped.listings()], ["https://b.test/1"])
        # takedown by publisher or URL
        self.fetch([item("https://a.test/5", source="Weichert"), item("https://a.test/6", source="Movoto")],
                   lambda u: (JPEG, "jpg"))
        self.assertEqual(self.hold.purge_where("philadelphia", source="weichert"), 1)
        self.assertEqual(self.hold.purge_where("philadelphia", url="https://a.test/6"), 1)
        self.hold.purge_all()
        self.assertFalse(Path(self.tmp, "hold").exists())


class TaggerTests(unittest.TestCase):
    def rows(self):
        return [{"url": "https://a.test/h", "address": "1 Main St Philadelphia, PA 19123", "date": "2026-11-23",
                 "price": 300000, "source": "Movoto", "photos": [f"https://img.test/h-{i}.jpg" for i in range(4)],
                 "description": "Sold as-is."}]

    def test_tagger_prefers_held_photos_and_deletes_them_after(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_city(root, "philadelphia", self.rows())
            hold = PhotoHold(root / "hold")
            _, plan = la.analyse_city(root, "philadelphia", dry_run=True, now=NOW, log=lambda *_: None)
            self.assertEqual(plan["planned"][0]["photoUrls"], self.rows()[0]["photos"])
            hold.fetch("philadelphia", plan["planned"], loader=lambda u: (JPEG, "jpg"), robots=AllowAll(), now=NOW,
                       log=lambda *_: None)

            def publisher(url):
                raise AssertionError("fetched from the publisher although the photo was held")
            backend = FakeBackend([ok(good_tags(4))])
            doc, report = la.analyse_city(root, "philadelphia", backend=backend, loader=publisher, cropper=None,
                                          hold=hold, now=NOW, log=lambda *_: None)
            self.assertEqual(report["tagged"], 1)
            self.assertEqual(backend.seen_files[0], [f"photo-{i}.jpg" for i in range(4)])
            self.assertEqual(list(hold.listings()), [])                # deleted once tagged and cropped
            store = json.loads((root / "analysis" / "philadelphia.json").read_text())
            self.assertNotIn("photoUrls", json.dumps(store))

    def test_falls_back_to_publisher_for_missing_photos(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_city(root, "philadelphia", self.rows())
            hold = PhotoHold(root / "hold")
            fetched = []
            la.analyse_city(root, "philadelphia", backend=FakeBackend([ok(good_tags(4))]),
                            loader=lambda u: fetched.append(u) or (JPEG, "jpg"), cropper=None, hold=hold, now=NOW,
                            log=lambda *_: None)
            self.assertEqual(len(fetched), 4)

    def test_takedown_purges_held_photos(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_city(root, "philadelphia", self.rows())
            hold = PhotoHold(root / "hold")
            hold.fetch("philadelphia", [item("https://a.test/h", source="Movoto")], loader=lambda u: (JPEG, "jpg"),
                       robots=AllowAll(), now=NOW, log=lambda *_: None)
            with unittest.mock.patch("sys.stdout"):
                la.main(["--city", "philadelphia", "--output", tmp, "--remove-crops-source", "movoto",
                         "--photo-hold", str(root / "hold")])
            self.assertEqual(list(hold.listings()), [])


@unittest.skipUnless(shutil.which("convert") and shutil.which("identify"), "ImageMagick not installed")
class ShrunkCropTests(unittest.TestCase):
    def test_crop_from_shrunk_image_matches_the_box(self):
        with tempfile.TemporaryDirectory() as tmp:
            big = Path(tmp, "photo.png")
            big.write_bytes(png(2000, 1000))
            # paint a solid red block over the box x 0.5-0.7, y 0.2-0.6 of the original
            subprocess.run(["convert", f"png:{big}", "-fill", "#ff0000", "-draw", "rectangle 1000,200 1399,599",
                            f"png:{big}"], check=True)
            shrunk = Path(tmp, "held.jpg")
            shrunk.write_bytes(la.magick_shrinker(shutil.which("convert"))(big))
            size = subprocess.run(["identify", "-format", "%w %h", f"jpeg:{shrunk}"], capture_output=True, text=True)
            self.assertEqual(size.stdout.strip(), "1024 512")
            box = {"x": 0.5, "y": 0.2, "w": 0.2, "h": 0.4}             # what the model would report on the held image
            crop = Path(tmp, "crop.jpg")
            crop.write_bytes(la.magick_cropper(shutil.which("convert"))(shrunk, box))
            size = subprocess.run(["identify", "-format", "%w %h", f"jpeg:{crop}"], capture_output=True, text=True)
            self.assertEqual(size.stdout.strip(), "205 205")             # 0.2 x 1024, 0.4 x 512
            pixel = subprocess.run(["convert", f"jpeg:{crop}", "-format", "%[fx:int(255*p{100,100}.r)] "
                                    "%[fx:int(255*p{100,100}.g)]", "info:"], capture_output=True, text=True)
            r, g = map(int, pixel.stdout.split())
            self.assertGreater(r, 200)
            self.assertLess(g, 60)                                        # the crop shows the red block


class GuardTests(unittest.TestCase):
    def test_publish_guard(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            hold = PhotoHold(root / "hold")
            hold.fetch("philadelphia", [item()], loader=lambda u: (JPEG + b"x", "jpg"), robots=AllowAll(), now=NOW,
                       log=lambda *_: None)
            public, store = root / "public", root / "listing-analysis"
            (public / "analysis" / "crops" / "philadelphia").mkdir(parents=True)
            (public / "analysis" / "crops" / "philadelphia" / "c.jpg").write_bytes(b"\xff\xd8\xffcrop")
            (store / "crops" / "philadelphia").mkdir(parents=True)
            (store / "philadelphia.json").write_text(json.dumps({"results": {"u": {"positions": [0, 3]}}}))
            self.assertEqual(check_published(public, stores=[store], hold_root=root / "hold"), [])
            held_file = next((root / "hold").rglob("p00.jpg"))
            shutil.copy(held_file, public / "analysis" / "crops" / "philadelphia" / "leak.jpg")
            shutil.copy(held_file, public / "photo.jpg")
            shutil.copy(next((root / "hold").rglob(MANIFEST)), store / MANIFEST)
            (store / "philadelphia.json").write_text(json.dumps({"results": {"u": {"photoUrls": ["https://x.test/a.jpg"]}}}))
            problems = "\n".join(check_published(public, stores=[store], hold_root=root / "hold"))
            for text in ("leak.jpg: identical to a held photo", "photo.jpg: image outside a crops folder",
                         "hold-manifest.json: photo-hold manifest", "philadelphia.json: contains publisher photo URLs"):
                self.assertIn(text, problems)

    def test_real_trees_are_clean(self):
        # what the deploy path ships, as present in this checkout
        for public in ("web/public/data",):
            if Path(public).is_dir():
                self.assertEqual(check_published(public), [])


if __name__ == "__main__":
    unittest.main()
