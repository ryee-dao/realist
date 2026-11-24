"""The intake sample: the newest records the engine read on its last real pull of a city, with the verdict each got.

The harvest leaves one sample per source in `Context.intake`. `remember` keeps the newest of them per city in the
store (store/_feed/<city>.json), so a city whose sources were unchanged today keeps its last sample and the time it
was read. `publish` writes it for the site as <out>/<city>/intake.json. A city with no sample yet falls back to its
newest stored sales, labelled as such.
"""
import json
import os

SIZE = 60


def _feed_path(store, city):
    return os.path.join(store.root, "_feed", f"{city}.json")


def merge(samples, size=SIZE):
    """One list from several sources' samples: each source's newest records get an equal share (so a source whose
    newest rows are all kept cannot hide another's rejects), unused shares go to the rest; newest sale date first."""
    picked, quota = [], size // max(1, len(samples))
    for i, s in enumerate(samples):
        picked += [(i, j) for j in range(min(quota, len(s["records"])))]
    rest = sorted(((s["records"][j]["date"], -i, -j) for i, s in enumerate(samples) for j in range(len(s["records"]))
                   if (i, j) not in set(picked)), reverse=True)
    picked += [(-i, -j) for _, i, j in rest[:size - len(picked)]]
    rows = [{**samples[i]["records"][j], "source": samples[i]["source"]} for i, j in picked]
    order = {id(r): (r["date"], -i, -j) for r, (i, j) in zip(rows, picked)}
    return sorted(rows, key=lambda r: order[id(r)], reverse=True)


def remember(store, city, samples, run_id):
    """Store this run's sample for a city. Nothing read this run (all sources unchanged): keep the last one."""
    samples = [s for s in samples if s["records"]]
    if not samples:
        return None
    feed = {
        "city": city, "kind": "read", "runId": run_id,
        "readAt": max(s["readAt"] for s in samples),
        "sources": [s["source"] for s in samples],
        "samples": samples,          # kept per source; `publish` merges them
    }
    path = _feed_path(store, city)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(feed, f, indent=1, sort_keys=True)
    return feed


def read(store, city):
    try:
        with open(_feed_path(store, city), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def stored_fallback(store, city, size=SIZE):
    """The newest stored sales, for a city the engine has not sampled yet."""
    rows = []
    for month in sorted(store.months(city), reverse=True):
        rows += list(store.read_month(city, month).values())
        if len(rows) >= size * 3:
            break
    if not rows:
        return None
    rows.sort(key=lambda r: (r["date"], r["id"]), reverse=True)
    return {
        "city": city, "kind": "stored", "runId": None, "readAt": None, "sources": [],
        "records": [{"date": r["date"], "price": int(r["price"]) if r.get("price") not in (None, "") else None,
                     "address": r.get("address") or "", "kind": r.get("type") or "", "verdict": "keep",
                     "reason": None, "source": "stored"} for r in rows[:size]],
    }


def publish(store, city, out_dir):
    """Write <out>/<city>/intake.json, only when its content changed (no churn on rebuilds)."""
    feed = read(store, city)
    if feed and "samples" in feed:
        feed = {**{k: v for k, v in feed.items() if k != "samples"}, "records": merge(feed["samples"])}
    feed = feed or stored_fallback(store, city)
    if not feed:
        return None
    path = os.path.join(out_dir, city, "intake.json")
    text = json.dumps(feed, separators=(",", ":"), sort_keys=True)
    try:
        with open(path, encoding="utf-8") as f:
            if f.read() == text:
                return feed
    except OSError:
        pass
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return feed
