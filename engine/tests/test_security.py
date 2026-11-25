"""Hostile-input tests: publisher URLs, redirects, image files, model output, file names and query values."""
import gzip
import io
import os
import socket
import tempfile
import unittest
import unittest.mock
import urllib.request
from pathlib import Path
from types import SimpleNamespace

from engine import http, listing_analysis as la, netguard
from engine.listing_sources import browser
from engine.photo_hold import MANIFEST, PhotoHold, _key
from engine.sources import chicago
from engine.tests.test_listing_analysis import good_tags


def resolver(*addresses):
    return lambda host, port, type=0: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (a, port)) for a in addresses]


PUBLIC = resolver("93.184.216.34")


class CheckUrlTests(unittest.TestCase):
    def refused(self, url, resolve=PUBLIC):
        with self.assertRaises(netguard.UnsafeURL, msg=url):
            netguard.check_url(url, resolve)

    def test_a_public_https_url_passes(self):
        netguard.check_url("https://pi.movoto.com/p/1.jpg", PUBLIC)
        netguard.check_url("https://pi.movoto.com:443/p/1.jpg", PUBLIC)

    def test_schemes_ports_and_credentials_are_refused(self):
        for url in ("http://pi.movoto.com/p/1.jpg", "file:///etc/passwd", "ftp://x.test/a", "data:image/png;base64,AA",
                    "javascript:alert(1)", "https://user:pw@pi.movoto.com/a", "https://pi.movoto.com:8443/a",
                    "https://pi.movoto.com:99999/a", "https:///nohost", None, 5):
            self.refused(url)

    def test_local_and_private_addresses_are_refused(self):
        for url in ("https://169.254.169.254/latest/meta-data/", "https://127.0.0.1/a", "https://10.1.2.3/a",
                    "https://192.168.0.1/a", "https://172.16.0.9/a", "https://100.64.0.1/a", "https://0.0.0.0/a",
                    "https://[::1]/a", "https://[fe80::1]/a", "https://[::ffff:169.254.169.254]/a", "https://224.0.0.1/a",
                    "https://localhost/a", "https://metadata.google.internal/a", "https://printer.local/a"):
            self.refused(url)

    def test_a_name_that_resolves_to_a_private_address_is_refused(self):
        self.refused("https://images.example.com/a.jpg", resolver("169.254.169.254"))
        self.refused("https://images.example.com/a.jpg", resolver("93.184.216.34", "10.0.0.5"))   # any one is enough

    def test_redirects_are_checked_before_they_are_followed(self):
        handler = netguard._CheckedRedirects()
        req = urllib.request.Request("https://pi.movoto.com/p/1.jpg")
        for target in ("http://pi.movoto.com/p/1.jpg", "https://169.254.169.254/latest/meta-data/", "https://[::1]/x"):
            with self.assertRaises(netguard.UnsafeURL):
                handler.redirect_request(req, None, 302, "Found", {}, target)

    def test_unsafe_urls_are_refused_before_any_request(self):
        with unittest.mock.patch.object(netguard._OPENER, "open") as opened:
            for url in ("file:///etc/passwd", "https://127.0.0.1/x", "http://example.com/"):
                with self.assertRaises(netguard.UnsafeURL):
                    netguard.urlopen_public(urllib.request.Request(url), timeout=1)
            opened.assert_not_called()
        with self.assertRaises(ValueError):
            la.download_image("https://169.254.169.254/latest/meta-data/x.jpg", pause=lambda s: None)

    def test_browser_requests_to_private_hosts_are_aborted(self):
        calls = []
        route = lambda url, kind: SimpleNamespace(request=SimpleNamespace(url=url, resource_type=kind),
                                                  abort=lambda: calls.append(("abort", url)),
                                                  continue_=lambda: calls.append(("go", url)))
        hosts = netguard.HostCheck(lambda host, port, type=0: resolver("10.0.0.1" if host == "evil.test" else "93.184.216.34")(host, port))
        handle = browser.request_filter(hosts)
        for url, kind in (("https://www.movoto.com/x", "document"), ("https://evil.test/x", "fetch"),
                          ("http://www.movoto.com/x", "script"), ("https://169.254.169.254/x", "xhr"),
                          ("https://www.movoto.com/a.jpg", "image")):
            handle(route(url, kind))
        self.assertEqual([c[0] for c in calls], ["go", "abort", "abort", "abort", "abort"])


class FakePillow:
    """Records how Image.open is called; ``size`` is what the file declares."""
    class DecompressionBombError(Exception):
        pass

    def __init__(self, size=(1000, 800), bomb=False):
        self.size, self.bomb, self.calls = size, bomb, []

    def open(self, path, formats=None):
        self.calls.append(formats)
        if self.bomb:
            raise FakePillow.DecompressionBombError("too many pixels")
        return SimpleNamespace(size=self.size, close=lambda: None)


class ImageTests(unittest.TestCase):
    def test_pillow_opens_only_the_sniffed_formats(self):
        fake = FakePillow()
        la.SafeImage(fake).open("/x.jpg")
        self.assertEqual(fake.calls, [la.PILLOW_FORMATS])

    def test_decompression_bombs_raise_value_error(self):
        with self.assertRaises(ValueError):
            la.SafeImage(FakePillow(size=(20000, 20000))).open("/x.png")
        with self.assertRaises(ValueError):
            la.SafeImage(FakePillow(bomb=True)).open("/x.png")

    def test_imagemagick_gets_limits_and_an_explicit_absolute_input(self):
        seen = []

        def run(cmd, **kw):
            seen.append(cmd)
            out = b"100 80" if "-format" in cmd else b"\xff\xd8jpeg"
            return SimpleNamespace(returncode=0, stdout=out)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, "msl:photo-0.jpg")      # even an odd file name is read as a JPEG at its absolute path
            la.magick_cropper("/usr/bin/magick", run=run)(path, {"x": 0.1, "y": 0.1, "w": 0.5, "h": 0.5})
            la.magick_shrinker("/usr/bin/magick", run=run)(path)
        for cmd in seen:
            self.assertIn("width", cmd)
            self.assertIn("area", cmd)
            self.assertTrue(any(a.startswith("jpeg:/") and a.endswith("[0]") for a in cmd), cmd)


class ModelOutputTests(unittest.TestCase):
    TOKEN = "sk-" + "ant-oat01-" + "Q7x" * 30          # built at run time: no token-shaped literal in the repo

    def test_credential_like_text_is_never_published(self):
        jwt = "ey" + "J" + "hbGciOiJIUzI1NiJ9" + ".e30.abc"
        tags = good_tags(
            defects=[{"type": "water_stain_ceiling", "photo_index": 1, "evidence": f"stain {self.TOKEN}", "confidence": 0.9, "box": None},
                     {"type": "water_stain_ceiling", "photo_index": 1, "evidence": "brown ring stain", "confidence": 0.8, "box": None}],
            description_claims=[{"claim": jwt, "status": "not_visible"}, {"claim": "new roof 2024", "status": "not_visible"}],
            staging_labels=[{"photo_index": 0, "text": "gh" + "p_" + "a1B2" * 9}, {"photo_index": 0, "text": "Virtually staged"}])
        clean = la.validate_tags(tags, 2)
        self.assertEqual([d["evidence"] for d in clean["defects"]], ["brown ring stain"])
        self.assertEqual([c["claim"] for c in clean["description_claims"]], ["new roof 2024"])
        self.assertEqual([s["text"] for s in clean["staging_labels"]], ["Virtually staged"])
        self.assertIsNone(la.clean_item("a1b2c3d4e5f6a1b2c3d4e5"))

    def test_ordinary_evidence_still_passes(self):
        for text in ("brown ring stain on kitchen ceiling, about 2 ft wide", "floor-to-ceiling windows",
                     "peeling paint near the 2nd floor window", "Virtually staged"):
            self.assertTrue(la._publishable(text, sensitive=False), text)

    def test_error_messages_are_redacted(self):
        self.assertNotIn(self.TOKEN, la.redact(f"claude -p failed: here it is {self.TOKEN} done"))
        self.assertIn("usage limit|1764000000", la.redact("usage limit|1764000000"))

    def test_read_is_allowed_only_in_the_working_directory(self):
        cmd = la.ClaudeCLI().command()
        self.assertEqual(cmd[cmd.index("--allowedTools") + 1], "Read(./**)")
        self.assertEqual(cmd[cmd.index("--tools") + 1], "Read")
        self.assertEqual(cmd[cmd.index("--permission-mode") + 1], "dontAsk")

    def test_each_child_sees_only_its_own_credentials(self):
        env = {"CLAUDE_CODE_OAUTH_TOKEN": "c", "CODEX_API_KEY": "k", "CODEX_HOME": "/h", "GH_TOKEN": "g",
               "GITHUB_TOKEN": "g", "CODEX_SECRET_WRITER": "w", "HUD_CENSUS_API_KEY": "h", "ANTHROPIC_API_KEY": "a",
               "PATH": os.environ.get("PATH", "")}
        with unittest.mock.patch.dict(os.environ, env, clear=True):
            claude, codex = la.child_env("claude"), la.child_env("codex")
        self.assertEqual(set(claude), {"CLAUDE_CODE_OAUTH_TOKEN", "PATH"})
        self.assertEqual(set(codex), {"CODEX_API_KEY", "CODEX_HOME", "PATH"})


class FileNameTests(unittest.TestCase):
    def test_crop_names_are_hashes_only(self):
        self.assertTrue(la.crop_name_ok("0123456789abcdef0123.jpg"))
        for name in ("../../etc/passwd", "/abs.jpg", "a.jpg", "0123456789abcdef0123.jpg/../x", None, 3,
                     "0123456789abcdef0123.JPG", "..\\0123456789abcdef0123.jpg"):
            self.assertFalse(la.crop_name_ok(name), name)

    def test_a_manifest_cannot_point_outside_the_hold(self):
        with tempfile.TemporaryDirectory() as tmp:
            hold = PhotoHold(Path(tmp, "hold"))
            secret = Path(tmp, "secret.jpg")
            secret.write_bytes(b"\xff\xd8\xff secret")
            folder = Path(tmp, "hold", "philadelphia", _key("https://a.test/1"))
            folder.mkdir(parents=True)
            (folder / "p00.jpg").write_bytes(b"\xff\xd8\xff ok")
            (folder / MANIFEST).write_text('{"url": "https://a.test/1", "photos": {'
                                           '"https://i.test/0.jpg": {"file": "p00.jpg"}, '
                                           '"https://i.test/1.jpg": {"file": "../../../secret.jpg"}, '
                                           f'"https://i.test/2.jpg": {{"file": "{secret}"}}}}}}')
            self.assertIsNotNone(hold.held("philadelphia", "https://a.test/1", "https://i.test/0.jpg"))
            self.assertIsNone(hold.held("philadelphia", "https://a.test/1", "https://i.test/1.jpg"))
            self.assertIsNone(hold.held("philadelphia", "https://a.test/1", "https://i.test/2.jpg"))


class QueryValueTests(unittest.TestCase):
    def test_cursors_dates_and_strings(self):
        self.assertEqual(http.sql_int(12), 12)
        self.assertEqual(http.sql_int("12"), 12)
        for bad in ("1 or 1=1", "0) union select 1 --", None, True, 1.5, ""):
            with self.assertRaises(http.FetchError):
                http.sql_int(bad)
        self.assertEqual(http.sql_date("2024-05-01"), "2024-05-01")
        self.assertEqual(http.sql_date("2024-05-01T00:00:00.000"), "2024-05-01T00:00:00.000")
        for bad in ("2024-05-01' or '1'='1", "2024-05-01T23:59:59' --", "yesterday"):
            with self.assertRaises(http.FetchError):
                http.sql_date(bad)
        self.assertEqual(http.sql_text("O'Brien"), "'O''Brien'")

    def test_hostile_parcel_ids_stay_inside_their_quotes(self):
        wheres = []
        fake = lambda client, base, dataset, select, where, **kw: wheres.append(where) or iter(())
        with unittest.mock.patch.object(chicago, "socrata_pages", fake):
            list(chicago._fetch_by_pin(SimpleNamespace(client=None), "x", "pin", ["123", "1') or (1=1"], "true"))
        self.assertEqual(wheres, ["pin in ('1'') or (1=1','123')"])

    def test_responses_are_size_capped(self):
        bomb = gzip.compress(b"\0" * 5000)
        with self.assertRaises(http.FetchError):
            http._read_capped(io.BytesIO(bomb), True, limit=1000)
        with self.assertRaises(http.FetchError):
            http._read_capped(io.BytesIO(b"x" * 2000), False, limit=1000)
        self.assertEqual(http._read_capped(io.BytesIO(gzip.compress(b"[1]")), True, limit=1000), b"[1]")


if __name__ == "__main__":
    unittest.main()
