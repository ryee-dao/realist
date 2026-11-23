"""The harvest store: one gzipped CSV per city per month, upserted by record id.

Files are written deterministically (sorted rows, zeroed gzip timestamp), so a
month with no upstream change produces identical bytes and no git churn.
"""
import csv
import gzip
import io
import json
import os
from collections import defaultdict

from .schema import COLUMNS


class Store:
    def __init__(self, root):
        self.root = root
        os.makedirs(root, exist_ok=True)

    # ---- partitions -------------------------------------------------------
    def _path(self, city, month):
        return os.path.join(self.root, city, month[:4], f"{month[5:7]}.csv.gz")

    def months(self, city):
        base = os.path.join(self.root, city)
        out = []
        if os.path.isdir(base):
            for year in sorted(os.listdir(base)):
                ydir = os.path.join(base, year)
                if year.isdigit() and os.path.isdir(ydir):
                    out += [f"{year}-{f[:2]}" for f in sorted(os.listdir(ydir)) if f.endswith(".csv.gz")]
        return out

    def read_month(self, city, month):
        path = self._path(city, month)
        if not os.path.exists(path):
            return {}
        with gzip.open(path, "rt", encoding="utf-8", newline="") as f:
            return {row["id"]: row for row in csv.DictReader(f)}

    def _write_month(self, city, month, rows):
        path = self._path(city, month)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        buf = io.StringIO()
        w = csv.DictWriter(buf, COLUMNS, lineterminator="\n")
        w.writeheader()
        for key in sorted(rows):
            w.writerow(rows[key])
        data = buf.getvalue().encode("utf-8")
        with open(path + ".tmp", "wb") as raw:
            with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0, filename="") as gz:
                gz.write(data)
        os.replace(path + ".tmp", path)

    def upsert(self, city, sales):
        """Merge rows into the store. A blank incoming field never erases a stored value."""
        by_month = defaultdict(list)
        for s in sales:
            by_month[s["date"][:7]].append(s)
        counts = {"new": 0, "updated": 0, "unchanged": 0}
        for month, incoming in sorted(by_month.items()):
            rows = self.read_month(city, month)
            dirty = False
            for s in incoming:
                new = {c: "" if s.get(c) is None else str(s[c]) for c in COLUMNS}
                old = rows.get(new["id"])
                if old is None:
                    rows[new["id"]] = new
                    counts["new"] += 1
                    dirty = True
                    continue
                merged = {c: new[c] if new[c] != "" else old.get(c, "") for c in COLUMNS}
                if merged != old:
                    rows[new["id"]] = merged
                    counts["updated"] += 1
                    dirty = True
                else:
                    counts["unchanged"] += 1
            if dirty:
                self._write_month(city, month, rows)
        return counts

    def remove(self, city, drop, since=""):
        """Delete the stored rows `drop(row)` picks, in months from `since` on. Returns how many went."""
        removed = 0
        for month in self.months(city):
            if month < since:
                continue
            rows = self.read_month(city, month)
            keep = {k: r for k, r in rows.items() if not drop(r)}
            if len(keep) < len(rows):
                removed += len(rows) - len(keep)
                self._write_month(city, month, keep)
        return removed

    def iter_rows(self, city):
        """Every stored row for a city. If an id moved months, the later month wins."""
        seen = set()
        for month in reversed(self.months(city)):
            for key, row in self.read_month(city, month).items():
                if key not in seen:
                    seen.add(key)
                    yield row

    def summary(self, city):
        months = self.months(city)
        total, lo, hi = 0, None, None
        for month in months:
            rows = self.read_month(city, month)
            total += len(rows)
            for r in rows.values():
                d = r["date"]
                lo = d if lo is None or d < lo else lo
                hi = d if hi is None or d > hi else hi
        return {"rows": total, "min_date": lo, "max_date": hi, "months": len(months)}

    def boundary_path(self, city):
        return os.path.join(self.root, "_lookups", f"{city}_boundaries.geojson")

    # ---- lookups (side tables keyed by parcel) ------------------------------
    def read_lookup(self, name):
        path = os.path.join(self.root, "_lookups", f"{name}.csv.gz")
        if not os.path.exists(path):
            return {}
        with gzip.open(path, "rt", encoding="utf-8", newline="") as f:
            return {row["key"]: row for row in csv.DictReader(f)}

    def write_lookup(self, name, rows, columns):
        path = os.path.join(self.root, "_lookups", f"{name}.csv.gz")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        buf = io.StringIO()
        w = csv.DictWriter(buf, ["key"] + columns, lineterminator="\n", extrasaction="ignore")
        w.writeheader()
        for key in sorted(rows):
            w.writerow({"key": key, **{c: rows[key].get(c, "") for c in columns}})
        with open(path + ".tmp", "wb") as raw:
            with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0, filename="") as gz:
                gz.write(buf.getvalue().encode("utf-8"))
        os.replace(path + ".tmp", path)

    # ---- state and run log --------------------------------------------------
    def read_state(self):
        path = os.path.join(self.root, "state.json")
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        return {}

    def write_state(self, state):
        with open(os.path.join(self.root, "state.json"), "w", encoding="utf-8") as f:
            json.dump(state, f, indent=1, sort_keys=True)

    def read_runs(self):
        path = os.path.join(self.root, "runs.jsonl")
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]

    def append_run(self, report, keep=400):
        runs = (self.read_runs() + [report])[-keep:]
        with open(os.path.join(self.root, "runs.jsonl"), "w", encoding="utf-8") as f:
            for r in runs:
                f.write(json.dumps(r, sort_keys=True) + "\n")
