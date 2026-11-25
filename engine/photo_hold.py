"""A private, short-term holding area for listing photos queued for tagging. Standard library only.

    python3 -m engine.photo_hold --status                       sizes and listings held
    python3 -m engine.photo_hold --purge-all                    delete everything held
    python3 -m engine.photo_hold --check web/public/data store/listing-analysis   publish guard (exit 1 on a problem)

Why: photo tagging should not depend on the publisher being reachable at tagging time, and a gallery that only
partly downloads should resume on the next run instead of starting again.

What is held, and for how long
  - Only photos of Philadelphia listings in the tagger's queue (same order: distressed / asking well under the
    estimate first), never from PHOTO_DISABLED_SOURCES, at most ``listing_limit`` listings and ``byte_budget`` bytes
    a run, and only the photos the tagger will look at (``choose_photos`` with the same photos-per-listing).
  - Each photo is shrunk on save to a JPEG of at most HOLD_MAX_SIDE (1024) px on the longest side at quality 80,
    with no metadata (Pillow, else ImageMagick). With neither, the original is kept and counts against the budgets.
  - A listing's photos are deleted as soon as it has been tagged; also when it leaves the listings, is more than
    RETENTION_DAYS (30) past its list date, or has been held MAX_HOLD_DAYS (14) without being tagged. The whole
    hold is capped at ``max_bytes`` (2 GB), oldest listing first. Takedowns (--remove-crops-source/--remove-crops-url
    in engine.listing_analysis) purge held photos too.
  - The hold lives outside web/public and outside anything committed or deployed (default ``photo-hold/``, in
    .gitignore). In GitHub it is carried only in the Actions cache (``photo-hold-v1-``), never on the data branch, in
    an artifact or in the Pages artifact; ``check_published`` fails the build if held photos show up there.

How it fetches
  The tagger's own downloader (honest User-Agent, 1 s pacing, 5 MB cap, image types checked by magic bytes), after
  the image host's robots.txt allows it. A 403 or 429 stops fetching from that host for the run and starts a
  cool-down of 1, 3, then 7 days for consecutive refusals (the same steps as engine.live_listings); there are no
  retries around a refusal. Each listing keeps a manifest (photo URL -> file, bytes, fetched time, failures), so a
  run fetches only what is still missing, and a listing is ready when every wanted photo is held.

Crops stay consistent: the tagger copies the held (shrunk) file into its working folder, the model sees exactly that
image, and defect crops are cut from the same file, so the normalised boxes and the crop source agree.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.request
import urllib.robotparser
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

from .netguard import urlopen_public

HOLD_DIR = Path("photo-hold")
MAX_BYTES = 2_000_000_000
MAX_HOLD_DAYS = 14
RETENTION_DAYS = 30
COOL_DOWN_DAYS = (1, 3, 7)        # same steps as engine.live_listings
MANIFEST = "hold-manifest.json"   # marker file name the publish guard looks for
USER_AGENT = "Realist/2.0 (public housing data research)"
ROBOTS_AGENT = "Realist"
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
HELD_NAME = re.compile(r"p\d{2,4}\.(?:jpg|jpeg|png|gif|webp)")
CROP_MAX_BYTES = 200_000          # a published crop is a <= 320 px JPEG; anything bigger is not a crop


class HostBlocked(Exception):
    pass


def _key(url):
    return hashlib.sha256(url.encode()).hexdigest()[:20]


def _write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False) as tmp:
        json.dump(data, tmp, separators=(",", ":"))
    os.replace(tmp.name, path)


def _read_json(path):
    try:
        data = json.loads(Path(path).read_text())
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


class Robots:
    """robots.txt per image host, read once per run. A missing file (404) allows; an unreadable one disallows."""

    def __init__(self, read=None):
        self.read = read or self._read
        self.parsers = {}

    @staticmethod
    def _read(url):
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urlopen_public(request, timeout=12) as resp:   # the host comes from a publisher's photo URL
                return resp.read(500_000).decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            if exc.code == 404 or exc.code == 410:
                return ""
            raise

    def allowed(self, url):
        parts = urlsplit(url)
        host = f"{parts.scheme}://{parts.netloc}"
        if host not in self.parsers:
            parser = urllib.robotparser.RobotFileParser()
            try:
                parser.parse((self.read(host + "/robots.txt") or "").splitlines())
            except (OSError, ValueError):
                parser = None
            self.parsers[host] = parser
        parser = self.parsers[host]
        return parser is not None and parser.can_fetch(ROBOTS_AGENT, url)


class PhotoHold:
    def __init__(self, root=HOLD_DIR, max_bytes=MAX_BYTES):
        self.root = Path(root)
        self.max_bytes = max_bytes

    # ---- layout --------------------------------------------------------------------------------------------
    def _dir(self, city, listing_url):
        return self.root / city / _key(listing_url)

    def manifest(self, city, listing_url):
        return _read_json(self._dir(city, listing_url) / MANIFEST) or {
            "url": listing_url, "photos": {}, "createdAt": None}

    def _save(self, city, listing_url, m):
        _write_json(self._dir(city, listing_url) / MANIFEST, m)

    def listings(self, city=None):
        cities = [city] if city else [p.name for p in self.root.iterdir() if p.is_dir()] if self.root.is_dir() else []
        for c in cities:
            base = self.root / c
            if base.is_dir():
                for d in base.iterdir():
                    m = _read_json(d / MANIFEST)
                    if m:
                        yield c, d, m

    def size(self):
        return sum(f.stat().st_size for f in self.root.rglob("*") if f.is_file()) if self.root.is_dir() else 0

    def _hosts_path(self):
        return self.root / "hosts.json"

    # ---- reading -------------------------------------------------------------------------------------------
    def held(self, city, listing_url, photo_url):
        entry = self.manifest(city, listing_url)["photos"].get(photo_url) or {}
        name = entry.get("file")
        # the manifest names a file in this listing's folder, as fetch writes it (p00.jpg); never a path
        path = self._dir(city, listing_url) / name if isinstance(name, str) and HELD_NAME.fullmatch(name) else None
        return path if path and path.is_file() else None

    def ready(self, city, listing_url, photo_urls):
        return all(self.held(city, listing_url, u) for u in photo_urls)

    def loader(self, city, listing_url, fallback):
        """A tagger loader: the held file when there is one, else ``fallback`` (the publisher)."""
        def load(photo_url):
            path = self.held(city, listing_url, photo_url)
            if path:
                return path.read_bytes(), path.suffix.lstrip(".")
            return fallback(photo_url)
        return load

    # ---- fetching ------------------------------------------------------------------------------------------
    def fetch(self, city, items, *, loader, shrink=None, robots=None, listing_limit=40, byte_budget=300_000_000,
              now=None, log=print):
        """Fetch the missing photos of ``items`` (tagger queue order): dicts with url, photoUrls, source, date.

        ``loader(url) -> (bytes, ext)`` is the tagger's downloader; ``shrink(path) -> jpeg bytes`` is optional.
        Returns a report: listings, photos and bytes fetched, ready listings, hosts stopped, failures.
        """
        from .listing_analysis import photos_disabled, sniff_image   # late import: listing_analysis imports this module

        now = now or datetime.now(timezone.utc)
        robots = robots or Robots()
        hosts = _read_json(self._hosts_path()) or {}
        stopped, spent = set(), 0
        report = {"listings": 0, "photos": 0, "bytes": 0, "ready": 0, "stoppedHosts": [], "failures": 0,
                  "skipped": 0, "budgetReached": False}
        for item in items[:listing_limit]:
            if photos_disabled(item.get("source")):
                report["skipped"] += 1
                continue
            m = self.manifest(city, item["url"])
            m.update(url=item["url"], source=item.get("source"), date=item.get("date"), address=item.get("address"))
            m["createdAt"] = m.get("createdAt") or now.isoformat()
            m["wanted"] = list(item["photoUrls"])
            report["listings"] += 1
            for n, photo_url in enumerate(item["photoUrls"]):
                if self.held(city, item["url"], photo_url):
                    continue
                host = urlsplit(photo_url).netloc
                record = hosts.setdefault(host, {})
                if host in stopped or (record.get("coolUntil") or "") > now.isoformat():
                    continue
                entry = m["photos"].setdefault(photo_url, {"failures": 0})
                if not robots.allowed(photo_url):
                    entry.update(lastError="robots.txt disallows", failures=entry["failures"] + 1)
                    report["failures"] += 1
                    continue
                try:
                    body, ext = loader(photo_url)
                except urllib.error.HTTPError as exc:
                    if exc.code in (403, 429):
                        record["blocks"] = record.get("blocks", 0) + 1
                        days = COOL_DOWN_DAYS[min(record["blocks"], len(COOL_DOWN_DAYS)) - 1]
                        record.update(coolUntil=(now + timedelta(days=days)).isoformat(), lastError=f"HTTP {exc.code}")
                        stopped.add(host)
                        log(f"{city}: {host} answered HTTP {exc.code}; no more photos from it for {days} day(s)")
                    entry.update(lastError=f"HTTP {exc.code}", failures=entry["failures"] + 1)
                    report["failures"] += 1
                    continue
                except (OSError, ValueError) as exc:
                    entry.update(lastError=str(exc)[:200], failures=entry["failures"] + 1)
                    report["failures"] += 1
                    continue
                record["blocks"] = 0
                data, suffix = body, ext
                if shrink is not None:
                    with tempfile.TemporaryDirectory(prefix="realist-hold-") as tmp:
                        src = Path(tmp, f"in.{ext}")
                        src.write_bytes(body)
                        try:
                            data, suffix = shrink(src), "jpg"
                        except (OSError, ValueError) as exc:
                            log(f"{city}: could not shrink a photo, keeping the original ({exc})")
                if not sniff_image(data):
                    entry.update(lastError="not an image after shrinking", failures=entry["failures"] + 1)
                    continue
                if spent + len(data) > byte_budget:
                    report["budgetReached"] = True
                    break
                d = self._dir(city, item["url"])
                d.mkdir(parents=True, exist_ok=True)
                name = f"p{n:02d}.{suffix}"
                (d / name).write_bytes(data)
                entry.update(file=name, bytes=len(data), fetchedAt=now.isoformat(), lastError=None)
                spent += len(data)
                report["photos"] += 1
            self._save(city, item["url"], m)
            if self.ready(city, item["url"], item["photoUrls"]):
                report["ready"] += 1
            if report["budgetReached"]:
                break
        report["bytes"] = spent
        report["stoppedHosts"] = sorted(stopped)
        _write_json(self._hosts_path(), hosts)
        self.enforce_cap(log=log)
        return report

    # ---- deleting ------------------------------------------------------------------------------------------
    def purge(self, city, listing_url):
        d = self._dir(city, listing_url)
        if d.is_dir():
            shutil.rmtree(d)
            return True
        return False

    def purge_where(self, city=None, source=None, url=None):
        """Delete held listings by publisher and/or listing URL (takedowns). Returns how many were removed."""
        from .listing_analysis import _source_key
        gone = 0
        for c, d, m in list(self.listings(city)):
            if (source and _source_key(m.get("source")) == _source_key(source)) or (url and m.get("url") == url):
                shutil.rmtree(d)
                gone += 1
        return gone

    def purge_all(self):
        if self.root.is_dir():
            shutil.rmtree(self.root)

    def expire(self, city, active, now=None, log=print):
        """Delete listings no longer in ``active`` (url -> list date), more than RETENTION_DAYS past their list
        date, or held more than MAX_HOLD_DAYS untagged; then enforce the size cap. Returns how many went."""
        now = now or datetime.now(timezone.utc)
        gone = 0
        for c, d, m in list(self.listings(city)):
            listed = active.get(m.get("url"), m.get("date"))
            try:
                too_old = listed and date.fromisoformat(str(listed)[:10]) < now.date() - timedelta(days=RETENTION_DAYS)
            except ValueError:
                too_old = False
            created = m.get("createdAt") or ""
            stale = created and created < (now - timedelta(days=MAX_HOLD_DAYS)).isoformat()
            if m.get("url") not in active or too_old or stale:
                shutil.rmtree(d)
                gone += 1
        self.enforce_cap(log=log)
        return gone

    def enforce_cap(self, log=print):
        """Evict whole listings, oldest first, until the hold is under ``max_bytes``."""
        total = self.size()
        if total <= self.max_bytes:
            return 0
        held = sorted(self.listings(), key=lambda t: t[2].get("createdAt") or "")
        gone = 0
        for _, d, _ in held:
            if total <= self.max_bytes:
                break
            total -= sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
            shutil.rmtree(d)
            gone += 1
        if gone:
            log(f"photo hold over {self.max_bytes / 1e9:.1f} GB: evicted {gone} listings, oldest first")
        return gone

    def status(self):
        out = {"bytes": self.size(), "listings": 0, "photos": 0, "byCity": {}}
        for c, d, m in self.listings():
            n = sum(1 for e in m.get("photos", {}).values() if e.get("file"))
            out["listings"] += 1
            out["photos"] += n
            out["byCity"][c] = out["byCity"].get(c, 0) + 1
        return out


def check_published(*roots, stores=(), hold_root=HOLD_DIR):
    """Problems if held photos (or anything else that is not a defect crop) could be published or committed.

    For every folder in ``roots`` (published or committed, e.g. web/public/data) and ``stores`` (analysis stores,
    e.g. store/listing-analysis):
    - no hold manifest anywhere;
    - image files only inside a ``crops`` folder, and no bigger than a crop (CROP_MAX_BYTES);
    - no file byte-identical to a held photo.
    For ``stores`` also: no JSON mentions a publisher photo URL ("photoUrls" or an http(s) image link). The
    harvester's own listing snapshots carry photo URLs by design and are not checked for them.
    """
    held = set()
    hold_root = Path(hold_root)
    if hold_root.is_dir():
        for f in hold_root.rglob("*"):
            if f.is_file() and f.suffix.lower() in IMAGE_SUFFIXES:
                held.add(hashlib.sha256(f.read_bytes()).hexdigest())
    problems = []
    store_roots = {Path(p) for p in stores}
    for root in [Path(p) for p in roots] + sorted(store_roots):
        if not root.exists():
            continue
        for f in root.rglob("*"):
            if not f.is_file():
                continue
            rel = f.relative_to(root).as_posix()
            if f.name == MANIFEST:
                problems.append(f"{root}/{rel}: photo-hold manifest")
            if f.suffix.lower() in IMAGE_SUFFIXES:
                if "crops" not in rel.split("/")[:-1]:
                    problems.append(f"{root}/{rel}: image outside a crops folder")
                elif f.stat().st_size > CROP_MAX_BYTES:
                    problems.append(f"{root}/{rel}: larger than a defect crop")
                if held and hashlib.sha256(f.read_bytes()).hexdigest() in held:
                    problems.append(f"{root}/{rel}: identical to a held photo")
            if root in store_roots and f.suffix == ".json" and f.stat().st_size < 50_000_000:
                text = f.read_text(errors="replace")
                if '"photoUrls"' in text or _has_image_url(text):
                    problems.append(f"{root}/{rel}: contains publisher photo URLs")
    return problems


def _has_image_url(text):
    import re
    return bool(re.search(r"https?://[^\"\s]+\.(?:jpe?g|png|gif|webp)\b", text, re.I))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--hold", type=Path, default=HOLD_DIR)
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--purge-all", action="store_true", help="delete every held photo")
    parser.add_argument("--check", nargs="+", metavar="DIR", help="publish guard: exit 1 if held photos could leak")
    parser.add_argument("--check-store", nargs="+", default=[], metavar="DIR",
                        help="analysis stores to check too (also for publisher photo URLs)")
    args = parser.parse_args(argv)
    hold = PhotoHold(args.hold)
    if args.purge_all:
        hold.purge_all()
        print(f"photo hold {args.hold} purged")
    if args.check:
        problems = check_published(*args.check, stores=args.check_store, hold_root=args.hold)
        for p in problems:
            print(f"::error::{p}" if os.environ.get("GITHUB_ACTIONS") else p)
        if problems:
            return 1
        print(f"publish guard: no held photos under {', '.join(args.check + args.check_store)}")
    if args.status or not (args.purge_all or args.check):
        s = hold.status()
        print(f"photo hold {args.hold}: {s['listings']} listings, {s['photos']} photos, {s['bytes'] / 1e6:.1f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
