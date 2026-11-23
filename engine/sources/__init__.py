"""City adapters. Each one pulls an open public dataset into the shared schema."""
from dataclasses import dataclass, field

from ..schema import Normalizer


@dataclass
class Context:
    client: object
    store: object
    state: dict          # this city's persisted state (source fingerprints etc.)
    since: str           # ISO date: re-pull everything sold on or after this day
    full: bool           # True on a backfill run
    today: object
    log: object = print
    reports: list = field(default_factory=list)


class SourceRun:
    """Tracks one source during a run and writes its rows to the store in batches."""

    def __init__(self, ctx, city, name, dataset):
        self.ctx, self.city = ctx, city
        self.norm = Normalizer(ctx.today)
        self.batch = []
        self.report = {
            "name": name, "dataset": dataset, "fetched": 0, "kept": 0,
            "new": 0, "updated": 0, "unchanged": 0, "rejected": {}, "skipped": None,
        }
        self._req0 = ctx.client.stats["requests"]

    def add(self, sale):
        if sale:
            self.batch.append(sale)
            if len(self.batch) >= 50000:
                self.flush()

    def flush(self):
        if self.batch:
            counts = self.ctx.store.upsert(self.city, self.batch)
            for k, v in counts.items():
                self.report[k] += v
            self.report["kept"] += len(self.batch)
            self.batch = []

    def skip(self, why):
        self.report["skipped"] = why
        return self.finish()

    def finish(self):
        self.flush()
        self.report["fetched"] = self.norm.seen
        self.report["rejected"] = dict(self.norm.rejected.most_common())
        self.report["requests"] = self.ctx.client.stats["requests"] - self._req0
        # remember the last real pull, so a day when the source is unchanged can still show what it holds
        pulls = self.ctx.state.setdefault("last_pull", {})
        if self.report["skipped"]:
            self.report["last"] = pulls.get(self.report["name"])
        else:
            pulls[self.report["name"]] = {k: self.report[k] for k in ("fetched", "kept", "rejected")} | {"date": self.ctx.today.isoformat()}
        self.ctx.reports.append(self.report)
        r = self.report
        if r["skipped"]:
            self.ctx.log(f"  {r['name']}: skipped ({r['skipped']})")
        else:
            self.ctx.log(f"  {r['name']}: fetched {r['fetched']:,}, kept {r['kept']:,} "
                         f"({r['new']:,} new, {r['updated']:,} updated), {r['requests']} requests")
        return r
