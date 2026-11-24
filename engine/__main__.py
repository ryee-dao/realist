"""Command line for the Realist engine.

    python -m engine run                 harvest every city, check quality, build the site data
    python -m engine run --city nyc      one city only
    python -m engine run --full          re-pull the whole history instead of the recent window
    python -m engine run --rebuild       wipe the stored sales and pull everything again
    python -m engine build               rebuild the site data from the store without fetching

Exit code 1 means a quality check failed and the output should not be published.
"""
import argparse
import datetime as dt
import os
import shutil
import sys
import time

from . import analytics, context, intake, listing_cards
from .cities import CITIES
from .http import Client
from .sources import Context
from .store import Store

BACKFILL_START = "2019-01-01"
LOOKBACK_DAYS = 240     # deeds are recorded late; re-pull this window every run


def log(msg):
    print(msg, flush=True)


def harvest(store, cities, full, today, client, run_id=None):
    state = store.read_state()
    out, rent_cache = {}, {}
    for city in cities:
        log(f"[{city}] harvesting")
        city_state = state.setdefault(city, {})
        # a history pull that was cut short must be repeated, not mistaken for a finished one
        is_full = full or not city_state.get("backfilled")
        since = BACKFILL_START if is_full else (today - dt.timedelta(days=LOOKBACK_DAYS)).isoformat()
        ctx = Context(client=client, store=store, state=city_state, since=since, full=is_full, today=today, log=log)
        t0 = time.time()
        error = None
        try:
            CITIES[city]["harvest"](ctx)
            if is_full:
                city_state["backfilled"] = True
        except Exception as e:   # one city failing must not lose the others' work
            error = f"{type(e).__name__}: {e}"
            log(f"  FAILED: {error}")
        intake.remember(store, city, ctx.intake, run_id)
        context_errors = context.harvest(ctx, city, rent_cache)
        out[city] = {"mode": "full" if is_full else "incremental", "since": since, "sources": ctx.reports,
                     "seconds": round(time.time() - t0, 1), "error": error, "context_errors": context_errors}
    return out, state


def quality_checks(facts, harvested, previous, today):
    checks = []

    def add(city, name, status, detail):
        checks.append({"city": city, "name": name, "status": status, "detail": detail})

    for city, f in facts.items():
        h = harvested.get(city)
        if h and h["error"]:   # the stored data is intact, so the site can still update; freshness catches a long outage
            add(city, "Harvest completed", "warn", f"source unavailable this run, showing stored data ({h['error']})")
        elif h:
            add(city, "Harvest completed", "pass", f"{sum(s['fetched'] for s in h['sources'] if not s.get('context')):,} sale records read")
        if h and h.get("context_errors"):
            add(city, "Crime, population and rent data", "warn", "; ".join(h["context_errors"])[:240])
        elif h:
            cx = f.get("context", {})
            add(city, "Crime, population and rent data", "pass",
                f"{cx.get('crimeAreas', 0)} areas with a crime rate, {cx.get('rentAreas', 0)} with a rent estimate")
        before = (previous or {}).get("cities", {}).get(city, {}).get("rows")
        if before:
            ok = f["rows"] >= before * 0.98
            add(city, "No records lost", "pass" if ok else "fail", f"{before:,} before, {f['rows']:,} now")
        age = (today - dt.date.fromisoformat(f["latestSale"])).days
        add(city, "Data is fresh", "pass" if age <= 150 else "warn" if age <= 240 else "fail",
            f"latest sale {f['latestSale']} ({age} days ago; public records lag by weeks)")
        yoy = f["kpi"]["medianYoY"]
        sane = yoy is not None and -40 <= yoy <= 60
        add(city, "Median price is plausible", "pass" if sane else "fail",
            f"${f['kpi']['median']:,} ({yoy:+.1f}% vs a year earlier)" if yoy is not None else "not enough sales")
        cov = f["coverage"]
        add(city, "Location coverage", "pass" if cov["area"] >= 90 else "warn" if cov["area"] >= 70 else "fail",
            f"{cov['area']}% of the last year's sales have an area, {cov['coordinates']}% have coordinates")
    for city, h in harvested.items():
        if city not in facts:
            add(city, "Harvest completed", "fail", h["error"] or "no usable records")
    return checks


def main(argv=None):
    p = argparse.ArgumentParser(prog="engine", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command", choices=["run", "build"])
    p.add_argument("--city", action="append", choices=list(CITIES), help="limit to one city (repeatable)")
    p.add_argument("--full", action="store_true", help="re-pull the full history")
    p.add_argument("--rebuild", action="store_true", help="wipe the chosen cities' stored sales and pull them again from scratch")
    p.add_argument("--store", default="store", help="harvest store directory")
    p.add_argument("--out", default="web/public/data", help="where the site's JSON is written")
    args = p.parse_args(argv)

    store = Store(args.store)
    today = dt.datetime.now(dt.timezone.utc).date()
    started = dt.datetime.now(dt.timezone.utc)
    client = Client(log=log)
    harvested, state = ({}, None)
    if args.command == "run":
        if args.rebuild:
            for city in args.city or list(CITIES):
                shutil.rmtree(os.path.join(args.store, city), ignore_errors=True)
        harvested, state = harvest(store, args.city or list(CITIES), args.full or args.rebuild, today, client,
                                   started.strftime("%Y%m%dT%H%M%SZ"))

    facts = {}
    for city in CITIES:
        log(f"[{city}] building analytics")
        f = analytics.build_city(store, city, args.out)
        if f:
            facts[city] = f
        intake.publish(store, city, args.out)
        listing_cards.refresh(args.out, [city], args.store, log=log)   # New listings page; skipped without listings.json

    runs = store.read_runs()
    ok = True
    if args.command == "run":
        checks = quality_checks(facts, harvested, None if args.rebuild else (runs[-1] if runs else None), today)
        ok = not any(c["status"] == "fail" for c in checks)
        finished = dt.datetime.now(dt.timezone.utc)
        for city, h in harvested.items():
            h["rows"] = facts.get(city, {}).get("rows", 0)
        report = {
            "run_id": started.strftime("%Y%m%dT%H%M%SZ"), "started": started.isoformat(timespec="seconds"),
            "finished": finished.isoformat(timespec="seconds"),
            "duration_s": round((finished - started).total_seconds(), 1),
            "mode": "full" if any(h["mode"] == "full" for h in harvested.values()) else "incremental",
            "cities": harvested, "checks": checks, "http": dict(client.stats), "ok": ok,
        }
        # cities not harvested this run keep their previous entry so "no records lost" still has a baseline
        if runs:
            for city, prev in runs[-1]["cities"].items():
                if city not in report["cities"]:
                    held = []
                    for src in prev["sources"]:      # show what each source last delivered, marked as not pulled today
                        last = src.get("last") or {"fetched": src["fetched"], "kept": src["kept"], "rejected": src["rejected"], "date": runs[-1]["started"][:10]}
                        held.append({**src, "fetched": 0, "kept": 0, "new": 0, "updated": 0, "unchanged": 0, "requests": 0, "rejected": {},
                                     "skipped": "not part of this run", "last": last})
                    report["cities"][city] = {**prev, "sources": held, "mode": "not run", "error": None, "context_errors": []}
        store.append_run(report)
        store.write_state(state)
        runs = store.read_runs()
        for c in checks:
            log(f"  [{c['status'].upper():4}] {c['city']}: {c['name']} - {c['detail']}")

    analytics.build_index(args.out, facts, runs, dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"))
    log(f"done: {'OK' if ok else 'QUALITY CHECK FAILED'} "
        f"({client.stats['requests']} requests, {client.stats['retries']} retries)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
