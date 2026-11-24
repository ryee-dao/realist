"""Per-area figures over time, written next to areas.json.

area-series.json  monthly sales and rolling medians per area, for the Market page's area picker
sold.json         what homes in each area sold for in recent complete months, for the Sold prices page

Both use only recorded sales up to the city's last complete month (analytics.last_complete_month),
except each area's latest individual sales, which are shown as recorded even while a month fills in.
"""
from collections import defaultdict

from . import stats
from .analytics import nice_step, pct_change, quantile, shift

SERIES_MONTHS = 60   # five years of monthly figures per area
ROLL = 3             # medians run over the 3 months to each month, so one thin month does not swing the line
FEW_POINT = 5        # a chart point with fewer sales behind it is published but flagged as few sales
THIN = 24            # a 12-month figure resting on fewer sales than this is flagged as few sales
KPI_YOY_MIN = 20     # a change in median with fewer sales than this on either side is flagged as few sales
YOY_FLOOR = 3        # below this on either side, no change is published at all
SOLD_MONTHS = 6      # Sold prices looks at the latest 6 complete months...
SOLD_MIN = 10        # ...and needs this many sales in them for a typical price
SOLD_YOY_MIN = 20    # each side, for "vs a year earlier"
LATEST_N = 5         # individual sales listed under each area


def _q(sorted_values, q):
    v = quantile(sorted_values, q)
    return None if v is None else round(v)


def _by_month(group):
    out = defaultdict(list)
    for r in group:
        out[r["month"]].append(r)
    return out


def _window(by_month, end, months):
    return [r for k in range(months) for r in by_month.get(shift(end, -k), [])]


def _stats(rows, min_n):
    prices = sorted(r["price"] for r in rows)
    ppsf = sorted(r["ppsf"] for r in rows if r["ppsf"])
    ok = len(prices) >= min_n
    return {
        "n": len(prices),
        "median": _q(prices, 0.5) if ok else None,
        "p25": _q(prices, 0.25) if ok else None,
        "p75": _q(prices, 0.75) if ok else None,
        "ppsf": _q(ppsf, 0.5) if len(ppsf) >= min_n else None,
        "nPpsf": len(ppsf),
    }


def area_series(rows, areas, months, through):
    """Monthly sales count and rolling 3-month median price and price per sq ft for every listed area,
    with the number of sales behind each point, plus 12-month headline figures. Nothing is withheld for
    being thin: each figure and point carries its sales count, and `few` flags those resting on few sales."""
    months = [m for m in months if m <= through][-SERIES_MONTHS:]
    groups = defaultdict(list)
    for r in rows:
        if r["key"] and r["month"] <= through:
            groups[r["key"]].append(r)
    out = {}
    for a in areas:
        by_month = _by_month(groups.get(a["key"], []))
        count, med, med_n, ppsf, ppsf_n = [], [], [], [], []
        for m in months:
            s = _stats(_window(by_month, m, ROLL), 1)
            count.append(len(by_month.get(m, [])))
            med.append(s["median"])
            med_n.append(s["n"])
            ppsf.append(s["ppsf"])
            ppsf_n.append(s["nPpsf"])
        now, was = _stats(_window(by_month, through, 12), 1), _stats(_window(by_month, shift(through, -12), 12), 1)
        both = min(now["n"], was["n"])
        both_ppsf = min(now["nPpsf"], was["nPpsf"])
        prices12 = sorted(r["price"] for r in _window(by_month, through, 12))
        step = nice_step((quantile(prices12, 0.95) or 1) / 14)
        hist = [0] * 16
        for p in prices12:
            hist[min(15, p // step)] += 1
        out[a["key"]] = {
            "count": count, "median": med, "medianN": med_n, "ppsf": ppsf, "ppsfN": ppsf_n,
            "kpi": {
                "sales12": now["n"], "salesBefore": was["n"],
                "salesYoY": pct_change(now["n"], was["n"]) if was["n"] >= YOY_FLOOR else None,
                "median12": now["median"], "medianYoY": pct_change(now["median"], was["median"]) if both >= YOY_FLOOR else None,
                "ppsf12": now["ppsf"], "ppsfN12": now["nPpsf"],
                "ppsfYoY": pct_change(now["ppsf"], was["ppsf"]) if both_ppsf >= YOY_FLOOR else None,
                "p25": now["p25"], "p75": now["p75"],
                "few": {
                    "median": now["n"] < THIN, "medianYoY": both < KPI_YOY_MIN,
                    "ppsf": now["nPpsf"] < THIN, "ppsfYoY": both_ppsf < KPI_YOY_MIN,
                    "salesYoY": was["n"] < 10, "range": now["n"] < THIN,
                },
            },
            "hist": {"step": step, "counts": hist},
        }
    return {"months": months, "rollMonths": ROLL, "fewBelow": FEW_POINT, "areas": out}


def sold_summary(rows, areas, through):
    """Per area: typical sold price, price per sq ft and middle half over the latest complete months,
    the change from the same months a year earlier, whether sales are speeding up or slowing, and the
    latest few recorded sales."""
    start, prev_end = shift(through, -(SOLD_MONTHS - 1)), shift(through, -12)
    prev_start = shift(prev_end, -(SOLD_MONTHS - 1))
    groups = defaultdict(list)
    for r in rows:
        if r["key"]:
            groups[r["key"]].append(r)
    out = []
    for a in areas:
        group = groups.get(a["key"], [])
        now = _stats([r for r in group if start <= r["month"] <= through], SOLD_MIN)
        was = _stats([r for r in group if prev_start <= r["month"] <= prev_end], SOLD_MIN)
        pace, _ = stats.count_trend(now["n"], was["n"])
        latest = sorted(group, key=lambda r: r["date"], reverse=True)[:LATEST_N]
        out.append({
            "key": a["key"],
            "n": now["n"], "nBefore": was["n"],
            "salesChange": pct_change(now["n"], was["n"]) if was["n"] >= 5 else None,
            "pace": pace if now["n"] + was["n"] >= 20 else None,   # 'up' / 'down' only when unlikely to be chance
            "median": now["median"], "p25": now["p25"], "p75": now["p75"], "ppsf": now["ppsf"],
            "medianYoY": pct_change(now["median"], was["median"]) if min(now["n"], was["n"]) >= SOLD_YOY_MIN else None,
            "latestSale": latest[0]["date"] if latest else None,
            "latest": [{"date": r["date"], "price": r["price"], "type": r["type"], "address": r["address"], "sqft": r["sqft"]}
                       for r in latest],
        })
    return {"from": start, "through": through, "months": SOLD_MONTHS, "before": [prev_start, prev_end], "areas": out}
