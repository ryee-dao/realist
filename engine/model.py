"""What drives recorded sale prices: a hedonic regression on log(price), and a backtest of it. Standard library only.

The model, for a sale i of a home in neighbourhood a in quarter t:

    log(price_i) = intercept + sum_k beta_k * x_ik + area_a + quarter_t + error_i

x holds the city's property characteristics (engine/characteristics.py): logs of floor area and lot area, and
categorical drivers coded against a reference level. A value that is not recorded gets its own "Not recorded" level
(or, for a log size, an indicator with the value set to the mean), so no sale is dropped for one missing field.
Neighbourhood and quarter enter as fixed effects. Everything is a sparse 0/1 column except the two log sizes, so X'X is
accumulated row by row over only the non-zero entries and the (~250 x 250) normal equations are solved by Cholesky.

Outliers: after a first fit, sales whose residual lies more than TRIM robust SDs (1.4826 x MAD) from the median residual
are dropped and the model refitted once. Standard errors are heteroskedasticity-robust (HC1) for the characteristics.

The backtest fits on sales through TRAIN_END and predicts later sales. Quarters after TRAIN_END are unseen, so two
versions are reported: "aligned" removes each method's median log error in each test quarter (one number per quarter
from that quarter's sales, the same treatment for every method, so it measures how well each ranks homes against each
other), and "forecast" uses no test-period information (the model carries the average 2024 quarter effect forward,
the price-per-square-foot baseline uses 2024 medians, the assessor's value is used as published).

No demographic variable enters: location enters only as the neighbourhood fixed effect.
"""
import datetime as dt
import json
import math
import os
import time
from collections import Counter, defaultdict

Z = 1.959963984540054
TRAIN_END = "2024-12-31"
RECENT_FROM = "2023-01-01"     # the "recent sales only" fit, to gauge how much current-record characteristics matter
MIN_LEVEL = 20                 # a category level with fewer training sales is folded into its driver's reference
MIN_AREA = 30                  # a neighbourhood with fewer training sales is pooled with the other small ones
TRIM = 3.0
RIDGE = 1e-3                   # tiny diagonal ridge, for numerical stability only
NOT_RECORDED = "Not recorded"
POOLED = "Smaller areas (pooled)"
PRICE_BANDS = [(0, 100_000, "Under $100k"), (100_000, 200_000, "$100k-200k"), (200_000, 350_000, "$200k-350k"),
               (350_000, 600_000, "$350k-600k"), (600_000, 10 ** 12, "$600k+")]


def quarter_of(date):
    return f"{date[:4]} Q{(int(date[5:7]) - 1) // 3 + 1}"


# ---- linear algebra ---------------------------------------------------------------------------------------------

def cholesky(a):
    """Lower-triangular L with L L' = a, for a symmetric positive definite matrix. None if not positive definite."""
    n = len(a)
    low = [[0.0] * n for _ in range(n)]
    for j in range(n):
        lj = low[j]
        s = a[j][j] - sum(v * v for v in lj[:j])
        if s <= 1e-12:
            return None
        d = math.sqrt(s)
        lj[j] = d
        for i in range(j + 1, n):
            li = low[i]
            li[j] = (a[i][j] - sum(x * y for x, y in zip(li[:j], lj[:j]))) / d
    return low


def chol_solve(low, b):
    n = len(b)
    z = [0.0] * n
    for i in range(n):
        li = low[i]
        z[i] = (b[i] - sum(li[k] * z[k] for k in range(i))) / li[i]
    x = [0.0] * n
    for i in range(n - 1, -1, -1):
        x[i] = (z[i] - sum(low[k][i] * x[k] for k in range(i + 1, n))) / low[i][i]
    return x


def chol_inverse(low):
    """a^-1 from its Cholesky factor: invert L, then (L^-1)' L^-1."""
    n = len(low)
    inv = [[0.0] * n for _ in range(n)]
    for j in range(n):
        inv[j][j] = 1.0 / low[j][j]
        for i in range(j + 1, n):
            li = low[i]
            inv[i][j] = -sum(li[k] * inv[k][j] for k in range(j, i)) / li[i]
    cols = [[inv[k][i] for k in range(n)] for i in range(n)]
    h = [[0.0] * n for _ in range(n)]
    for i in range(n):
        ci = cols[i]
        for j in range(i, n):
            cj = cols[j]
            v = sum(ci[k] * cj[k] for k in range(j, n))
            h[i][j] = h[j][i] = v
    return h


def solve_dense(a, b):
    low = cholesky(a)
    return chol_solve(low, b) if low else None


def accumulate(rows, p, weights=None):
    """X'X (full, symmetric) from sparse rows [(col, value), ...] sorted by column, optionally weighted per row."""
    a = [[0.0] * p for _ in range(p)]
    for i, nz in enumerate(rows):
        w = weights[i] if weights is not None else 1.0
        if w == 0:
            continue
        for k, (ca, va) in enumerate(nz):
            row, va = a[ca], va * w
            for cb, vb in nz[k:]:
                row[cb] += va * vb
    for i in range(p):
        for j in range(i + 1, p):
            a[j][i] = a[i][j]
    return a


def dot(nz, beta):
    return sum(beta[c] * v for c, v in nz)


def median(values):
    s = sorted(values)
    n = len(s)
    if not n:
        return None
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


# ---- design -----------------------------------------------------------------------------------------------------

class Design:
    """Column layout for a set of training records: drivers, neighbourhoods and quarters.

    records: dicts with "f" ({driver: value or None}), "area" and "period" (quarter label).
    """

    def __init__(self, spec, records):
        self.spec = spec
        self.names = ["Intercept"]
        self.groups = {}           # driver -> [column, ...]
        self.cont = {}             # log driver -> (value column, missing column or None, centre)
        self.levels = {}           # categorical driver -> {level: column}
        self.counts = {}           # driver -> Counter(level -> training sales)
        for g in spec:
            name = g["name"]
            vals = [r["f"].get(name) for r in records]
            if g["kind"] == "log":
                have = [v for v in vals if v is not None]
                if len(have) < MIN_LEVEL:
                    continue
                centre = sum(have) / len(have)
                cols = [self._add(f"{name}")]
                miss = None
                if len(vals) - len(have) >= MIN_LEVEL:
                    miss = self._add(f"{name}: {NOT_RECORDED}")
                    cols.append(miss)
                self.cont[name] = (cols[0], miss, centre)
                self.groups[name] = cols
                self.counts[name] = Counter({"recorded": len(have), NOT_RECORDED: len(vals) - len(have)})
            else:
                c = Counter(NOT_RECORDED if v is None else v for v in vals)
                self.counts[name] = c
                levels = {}
                for level in list(g.get("order", [])) + sorted(set(c) - set(g.get("order", []))):
                    if level != g["ref"] and c.get(level, 0) >= MIN_LEVEL and level not in levels:
                        levels[level] = self._add(f"{name}: {level}")
                if levels:
                    self.levels[name] = levels
                    self.groups[name] = list(levels.values())
        area_n = Counter(r["area"] for r in records)
        big = [a for a, n in area_n.most_common() if n >= MIN_AREA]
        self.area_ref = big[0] if big else None
        self.area_col = {}
        for a in sorted(big[1:]):
            self.area_col[a] = self._add(f"Area: {a}")
        if any(n < MIN_AREA for n in area_n.values()):
            self.area_col[POOLED] = self._add(f"Area: {POOLED}")
        self.area_n = area_n
        self.groups["Neighbourhood"] = list(self.area_col.values())
        self.periods = sorted({r["period"] for r in records})
        self.period_col = {q: self._add(f"Quarter: {q}") for q in self.periods[1:]}
        self.groups["Sale quarter"] = list(self.period_col.values())
        self.p = len(self.names)
        self._rows = {}

    def _add(self, name):
        self.names.append(name)
        return len(self.names) - 1

    def area_column(self, area):
        if area == self.area_ref:
            return None
        if area in self.area_col:
            return self.area_col[area]
        return self.area_col.get(POOLED)

    def row(self, rec, with_period=True, cache=True):
        """Sparse row for one record; a period the design has not seen gets no quarter column.

        Quarter columns come last in the layout, so appending one keeps the row sorted. Rows are cached by record id.
        """
        nz = self._rows.get(rec["id"])
        if nz is None:
            nz = [(0, 1.0)]
            f = rec["f"]
            for name, (col, miss, centre) in self.cont.items():
                v = f.get(name)
                if v is None:
                    if miss is not None:
                        nz.append((miss, 1.0))
                else:
                    nz.append((col, v - centre))
            for name, levels in self.levels.items():
                v = f.get(name)
                col = levels.get(NOT_RECORDED if v is None else v)
                if col is not None:
                    nz.append((col, 1.0))
            ac = self.area_column(rec["area"])
            if ac is not None:
                nz.append((ac, 1.0))
            nz.sort()
            if cache:
                self._rows[rec["id"]] = nz
        if with_period:
            pc = self.period_col.get(rec["period"])
            if pc is not None:
                return nz + [(pc, 1.0)]
        return nz


# ---- fitting ----------------------------------------------------------------------------------------------------

def fit(design, records, robust=True):
    """Least squares with one trimmed refit. Returns a dict with the coefficients and what the summaries need."""
    p = design.p
    rows = [design.row(r, cache=False) for r in records]     # not cached: a fit's rows are freed when it returns
    ys = [r["y"] for r in records]
    a = accumulate(rows, p)
    xy = [0.0] * p
    for nz, y in zip(rows, ys):
        for c, v in nz:
            xy[c] += v * y

    def solve(a, xy):
        ar = [row[:] for row in a]
        for i in range(1, p):
            ar[i][i] += RIDGE
        low = cholesky(ar)
        if low is None:
            raise ValueError("normal equations are not positive definite")
        return low, chol_solve(low, xy)

    _, beta = solve(a, xy)
    resid = [y - dot(nz, beta) for nz, y in zip(rows, ys)]
    centre = median(resid)
    scale = 1.4826 * median([abs(e - centre) for e in resid])
    keep = [abs(e - centre) <= TRIM * scale for e in resid]
    for nz, y, k in zip(rows, ys, keep):      # take the trimmed sales back out of X'X and X'y
        if k:
            continue
        for i, (ca, va) in enumerate(nz):
            xy[ca] -= va * y
            ra = a[ca]
            for cb, vb in nz[i:]:
                ra[cb] -= va * vb
                if cb != ca:
                    a[cb][ca] -= va * vb
    low, beta = solve(a, xy)
    resid = [y - dot(nz, beta) for nz, y in zip(rows, ys)]
    n = sum(keep)
    kept_y = [y for y, k in zip(ys, keep) if k]
    yy = sum(y * y for y in kept_y)
    mean_y = sum(kept_y) / n
    sse = sum(e * e for e, k in zip(resid, keep) if k)
    sst = yy - n * mean_y * mean_y
    h = chol_inverse(low)
    out = {"design": design, "beta": beta, "h": h, "a": a, "xy": xy, "yy": yy, "n": n, "trimmed": len(keep) - n,
           "sse": sse, "sst": sst, "r2": 1 - sse / sst, "rmse": math.sqrt(sse / n), "resid": resid, "keep": keep}
    se = {}
    if robust:
        # HC1 sandwich H (sum e^2 x x') H, for the characteristics' columns only (not every area and quarter)
        meat = accumulate(rows, p, [e * e if k else 0.0 for e, k in zip(resid, keep)])
        wanted = [c for g, cols in design.groups.items() if g not in ("Neighbourhood", "Sale quarter") for c in cols]
        factor = n / max(1, n - p)
        for c in wanted:
            hc = h[c]
            mh = [sum(mr[k] * hc[k] for k in range(p)) for mr in meat]
            se[c] = math.sqrt(max(0.0, factor * sum(hc[k] * mh[k] for k in range(p))))
    else:
        s2 = sse / max(1, n - p)
        se = {c: math.sqrt(max(0.0, s2 * h[c][c])) for c in range(p)}
    out["se"] = se
    return out


def restricted(model, cols):
    """Coefficients with the columns `cols` forced to zero (the drop-one model), from the full fit's inverse."""
    beta, h = model["beta"], model["h"]
    sub = [[h[i][j] for j in cols] for i in cols]
    w = solve_dense(sub, [beta[c] for c in cols])
    if w is None:
        return None
    out = beta[:]
    for i in range(len(beta)):
        out[i] -= sum(h[i][c] * wc for c, wc in zip(cols, w))
    for c in cols:
        out[c] = 0.0
    return out


def sse_of(model, beta):
    """In-sample sum of squared errors on the kept sales, from X'X and X'y (no pass over the data)."""
    a, xy = model["a"], model["xy"]
    quad = sum(beta[i] * sum(ar[j] * beta[j] for j in range(len(beta))) for i, ar in enumerate(a))
    return model["yy"] - 2 * sum(b * v for b, v in zip(beta, xy)) + quad


def pct(b):
    return round((math.exp(b) - 1) * 100, 1)


def effects(model):
    """Each driver level's effect on price in % (log drivers per +10%), with a 95% interval."""
    d, beta, se = model["design"], model["beta"], model["se"]
    out = {}
    for g in d.spec:
        name = g["name"]
        if name in d.cont:
            col, miss, _ = d.cont[name]
            k = math.log(1.1)
            b, s = beta[col], se.get(col, 0.0)
            out[name] = [{"level": g.get("unit", "+10%"), "pct": pct(b * k), "lo": pct((b - Z * s) * k),
                          "hi": pct((b + Z * s) * k), "n": d.counts[name]["recorded"], "elasticity": round(b, 3)}]
            continue
        if name not in d.levels:
            continue
        levels = d.levels[name]
        rows = []
        order = list(g.get("order", [])) + [lv for lv in levels if lv not in g.get("order", [])]
        for level in order:
            if level == NOT_RECORDED:
                continue
            n = d.counts[name].get(level, 0)
            if level == g["ref"]:
                rows.append({"level": level, "pct": 0.0, "lo": 0.0, "hi": 0.0, "n": n, "ref": True})
            elif level in levels:
                c = levels[level]
                b, s = beta[c], se.get(c, 0.0)
                rows.append({"level": level, "pct": pct(b), "lo": pct(b - Z * s), "hi": pct(b + Z * s), "n": n})
        if NOT_RECORDED in levels:
            c = levels[NOT_RECORDED]
            b, s = beta[c], se.get(c, 0.0)
            rows.append({"level": NOT_RECORDED, "pct": pct(b), "lo": pct(b - Z * s), "hi": pct(b + Z * s),
                         "n": d.counts[name].get(NOT_RECORDED, 0), "missing": True})
        out[name] = rows
    return out


def area_premiums(model, area_names=None):
    """Neighbourhood fixed effects, centred so the sales-weighted average neighbourhood is 0%."""
    d, beta = model["design"], model["beta"]
    raw = {a: (beta[c] if c is not None else 0.0) for a in d.area_n for c in [d.area_column(a)]}
    total = sum(d.area_n.values())
    mean = sum(raw[a] * n for a, n in d.area_n.items()) / total
    out = []
    for a, n in d.area_n.items():
        if n < MIN_AREA:
            continue
        out.append({"key": a, "name": (area_names or {}).get(a, a.split("|")[0]), "premium": pct(raw[a] - mean), "n": n})
    out.sort(key=lambda r: -r["premium"])
    return out, raw, mean


def weighted_quantile(pairs, q):
    pairs = sorted(pairs)
    total = sum(w for _, w in pairs)
    acc = 0
    for v, w in pairs:
        acc += w
        if acc >= q * total:
            return v
    return pairs[-1][0]


def time_effects(model):
    d, beta = model["design"], model["beta"]
    return {q: (beta[d.period_col[q]] if q in d.period_col else 0.0) for q in d.periods}


# ---- prediction and scoring -------------------------------------------------------------------------------------

def predict(model, records, beta=None, period_effect=None):
    """Log-price predictions. Unseen quarters take `period_effect` (a number, or {quarter: number})."""
    d = model["design"]
    beta = beta or model["beta"]
    te = {q: (beta[d.period_col[q]] if q in d.period_col else 0.0) for q in d.periods}
    out = []
    for r in records:
        base = dot(d.row(r, with_period=False), beta)
        q = r["period"]
        if q in te:
            out.append(base + te[q])
        elif isinstance(period_effect, dict):
            out.append(base + period_effect.get(q, 0.0))
        else:
            out.append(base + (period_effect or 0.0))
    return out


def align(errors, periods):
    """Remove the median log error within each quarter (one number per quarter)."""
    by_q = defaultdict(list)
    for e, q in zip(errors, periods):
        by_q[q].append(e)
    shift = {q: median(v) for q, v in by_q.items()}
    return [e - shift[q] for e, q in zip(errors, periods)], shift


def score(errors):
    """errors: log(predicted) - log(price). Typical (median absolute) % error, share within 10%/20%, median bias %."""
    n = len(errors)
    if not n:
        return {"n": 0, "mdape": None, "within10": None, "within20": None, "bias": None}
    ape = sorted(abs(math.exp(e) - 1) for e in errors)
    return {
        "n": n,
        "mdape": round(median(ape) * 100, 1),
        "within10": round(sum(1 for v in ape if v <= 0.10) / n * 100, 1),
        "within20": round(sum(1 for v in ape if v <= 0.20) / n * 100, 1),
        "bias": round((math.exp(median(errors)) - 1) * 100, 1),
    }


def mse(errors):
    return sum(e * e for e in errors) / len(errors) if errors else None


def forecast_level(model, n=4):
    """The average effect of the last n training quarters: the market level carried forward, no growth assumed."""
    d = model["design"]
    te = time_effects(model)
    last = d.periods[-n:]
    return sum(te[q] for q in last) / len(last)


def predictor_of(model, through=None, n=2):
    """The fitted coefficients in a compact, JSON-ready form, so a home outside the sales (a current listing) can be
    priced without refitting: log price = intercept + level + area + sum of driver terms (engine/listing_cards.py).

    `level` is the average quarter effect of the last n quarters that end on or before `through` (complete months
    only), so a quarter still filling in with late-recorded deeds does not set it. No growth is assumed after it.
    """
    d, beta = model["design"], model["beta"]
    te = time_effects(model)
    ends = lambda q: f"{q[:4]}-{int(q[-1]) * 3:02d}"
    done = [q for q in d.periods if through is None or ends(q) <= through] or d.periods
    last = done[-n:]
    spec = {g["name"]: g for g in d.spec}
    raw = {a: (beta[c] if c is not None else 0.0) for a in d.area_n for c in [d.area_column(a)]}
    total = sum(d.area_n.values()) or 1
    r6 = lambda v: round(v, 6)
    return {
        "basis": "features + neighbourhood + sale quarter, fitted on every modelled sale",
        "intercept": r6(beta[0]),
        "level": {"quarters": last, "effect": r6(sum(te[q] for q in last) / len(last))},
        "log": {name: {"beta": r6(beta[col]), "centre": r6(centre), "missing": r6(beta[miss]) if miss is not None else None}
                for name, (col, miss, centre) in d.cont.items()},
        "cat": {name: {"ref": spec[name].get("ref"), "levels": {lv: r6(beta[c]) for lv, c in levels.items()}}
                for name, levels in d.levels.items()},
        "areas": {a: r6(v) for a, v in sorted(raw.items()) if d.area_n[a] >= MIN_AREA},
        "pooled": r6(beta[d.area_col[POOLED]]) if POOLED in d.area_col else 0.0,
        "areaMean": r6(sum(raw[a] * k for a, k in d.area_n.items()) / total),
    }


def ppsf_baseline(train, test, since):
    """Neighbourhood median price per square foot (training sales from `since`) times the home's floor area."""
    by_area = defaultdict(list)
    city = []
    for r in train:
        if r["date"] >= since and r.get("sqft"):
            v = r["price"] / r["sqft"]
            if 10 <= v <= 5000:
                by_area[r["area"]].append(v)
                city.append(v)
    med = {a: median(v) for a, v in by_area.items() if len(v) >= 5}
    fallback = median(city)
    out = []
    for r in test:
        if not r.get("sqft") or fallback is None:
            out.append(None)
            continue
        out.append(math.log(med.get(r["area"], fallback) * r["sqft"]))
    return out


def band_of(price):
    for lo, hi, label in PRICE_BANDS:
        if lo <= price < hi:
            return label
    return PRICE_BANDS[-1][2]


# ---- nearby earlier sales --------------------------------------------------------------------------------------

NEAR_K = 15          # comparables per sale
NEAR_MIN = 5         # fewer than this and the sale falls back to its neighbourhood level (with an indicator)
NEAR_DAYS = 730      # comparables sold within the previous two years...
NEAR_KM = 1.5        # ...and within this distance
CELL_KM = 0.125      # grid cells of 125 m: small cells keep the scan close to the circle that holds the k nearest


def nearby_sales(records, k=NEAR_K):
    """For each record, the indices of up to k nearest sales dated strictly before it (within NEAR_DAYS and NEAR_KM),
    same home type where at least NEAR_MIN are found, else any type. Records must be sorted by date.

    A grid of 125 m cells holds earlier sales in date order; each search walks rings of cells outward and stops once
    k same-type sales lie inside the radius already covered, so the answer is exact. Sales of the same day are added
    only after that day is searched, so no sale sees itself or a same-day sale.
    """
    import bisect
    located = [r for r in records if r.get("lat") is not None]
    if not located:
        return [[] for _ in records]
    coslat = math.cos(math.radians(sum(r["lat"] for r in located) / len(located)))
    xy = [(r["lon"] * 111.2 * coslat, r["lat"] * 111.2) if r.get("lat") is not None else None for r in records]
    types = {}
    code = [types.setdefault(r["type"], len(types)) for r in records]
    grid = {}
    out = [[] for _ in records]
    max_ring = int(math.ceil(NEAR_KM / CELL_KM))
    lim = NEAR_KM * NEAR_KM

    def search(i):
        if xy[i] is None:
            return []
        x, y = xy[i]
        start = records[i]["ord"] - NEAR_DAYS
        cx, cy = int(math.floor(x / CELL_KM)), int(math.floor(y / CELL_KM))
        mine = code[i]
        found = []
        typed_d2 = []
        for ring in range(max_ring + 1):
            for gy in range(cy - ring, cy + ring + 1):
                edge = gy in (cy - ring, cy + ring)
                for gx in (range(cx - ring, cx + ring + 1) if edge else (cx - ring, cx + ring)):
                    cell = grid.get((gx, gy))
                    if not cell:
                        continue
                    ords, xs, ys, cs, idxs = cell
                    for t in range(bisect.bisect_left(ords, start), len(ords)):
                        d2 = (xs[t] - x) ** 2 + (ys[t] - y) ** 2
                        if d2 <= lim:
                            same = cs[t] == mine
                            found.append((d2, idxs[t], same))
                            if same:
                                typed_d2.append(d2)
            if len(typed_d2) >= k:
                reach = (ring * CELL_KM) ** 2
                if sum(1 for d2 in typed_d2 if d2 <= reach) >= k:
                    break
        found.sort()
        typed = [j for _, j, t in found if t][:k]
        if len(typed) >= NEAR_MIN:
            return typed
        return [j for _, j, _ in found[:k]]

    i, n = 0, len(records)
    while i < n:
        j = i
        while j < n and records[j]["date"] == records[i]["date"]:
            j += 1
        for t in range(i, j):
            out[t] = search(t)
        for t in range(i, j):
            if xy[t] is None:
                continue
            x, y = xy[t]
            cell = grid.setdefault((int(math.floor(x / CELL_KM)), int(math.floor(y / CELL_KM))), ([], [], [], [], []))
            for lst, v in zip(cell, (records[t]["ord"], x, y, code[t], t)):
                lst.append(v)
        i = j
    return out


def nearby_feature(comps, resid):
    """Median residual (log points) of each sale's comparables; None where there are too few."""
    return [median([resid[j] for j in c]) if len(c) >= NEAR_MIN else None for c in comps]


# ---- the whole analysis -----------------------------------------------------------------------------------------

NEARBY = {"name": "Nearby sales", "kind": "log", "unit": "+10% nearby prices"}
ASSESSOR = {"name": "Assessor's value", "kind": "log", "unit": "+10% assessed value"}


def records_from(rows, lookup, cfg, history=None):
    """Model records from loaded sales and the characteristics lookup. Returns (records, dropped counts).

    `value` is the assessor's current value; `prior` the value certified for the sale's own tax year (set during the
    year before), from the assessment history when there is one.
    """
    out, dropped = [], Counter()
    for r in rows:
        if not r.get("key"):
            dropped["no neighbourhood"] += 1
            continue
        ch = lookup.get(r.get("parcel") or "")
        if ch is None:
            dropped["no property record for the parcel"] += 1
            continue
        prior = None
        if history is not None and cfg.get("value_for_year"):
            prior = cfg["value_for_year"](history.get(r["parcel"]), int(r["date"][:4]))
        out.append({"id": r["id"], "date": r["date"], "ord": dt.date.fromisoformat(r["date"]).toordinal(),
                    "price": r["price"], "y": math.log(r["price"]), "type": r["type"], "area": r["key"],
                    "period": quarter_of(r["date"]), "sqft": r.get("sqft"), "lat": r.get("lat"), "lon": r.get("lon"),
                    "value": cfg["value"](ch) if cfg.get("value") else None, "prior": prior,
                    "f": cfg["features"](r, ch)})
    return out, dropped


def with_features(records, **extra):
    """Shallow copies of the records with extra driver values ({driver name: [value per record]}) layered over their
    features (a ChainMap, so 150k copies cost little memory)."""
    from collections import ChainMap
    return [dict(r, f=ChainMap({name: vals[i] for name, vals in extra.items()}, r["f"])) for i, r in enumerate(records)]


def residuals(model, records, n_train):
    """In-sample residuals for the first n_train records, forecast-level residuals for the rest."""
    rest = records[n_train:]
    pred = predict(model, rest, period_effect=forecast_level(model))
    return model["resid"] + [r["y"] - p for r, p in zip(rest, pred)]


METHOD_NAMES = {
    "assessor": "Assessor's value, set before the sale",
    "model": "Features + neighbourhood",
    "nearby": "Features + nearby sales",
    "adjusted": "Assessor's value + adjustments",
    "ppsf": "Area $/sq ft × size",
    "assessorNow": "Assessor's current value",
}


def backtest(preds, test, idx):
    """Scores for every method on the same test sales (indices `idx` into test). preds: {method: [log price or None]}."""
    sub = [test[i] for i in idx]
    periods = [r["period"] for r in sub]
    raw = {k: [p[i] - test[i]["y"] for i in idx] for k, p in preds.items()}
    aligned = {k: align(v, periods)[0] for k, v in raw.items()}
    methods = [{"id": k, "name": METHOD_NAMES.get(k, k), "aligned": score(aligned[k]), "forecast": score(raw[k])} for k in raw]

    def breakdown(label_of, order):
        groups = defaultdict(list)
        for j, r in enumerate(sub):
            groups[label_of(r)].append(j)
        out = []
        for label in order:
            g = groups.get(label)
            if g:
                out.append({"label": label, "n": len(g), **{k: score([aligned[k][j] for j in g]) for k in raw},
                            "forecast": {k: score([raw[k][j] for j in g]) for k in raw}})
        return out

    types = [t for t, _ in Counter(r["type"] for r in sub).most_common()]
    return {
        "trainThrough": TRAIN_END, "testFrom": test[0]["date"] if test else None,
        "testTo": max(r["date"] for r in test) if test else None,
        "testSales": len(test), "n": len(sub), "share": round(len(sub) / len(test) * 100, 1) if test else None,
        "methods": methods,
        "byType": breakdown(lambda r: r["type"], types),
        "byBand": breakdown(lambda r: band_of(r["price"]), [b[2] for b in PRICE_BANDS]),
    }


def importance(train_model, test_common, full=None):
    """Drop-one importance per driver: the rise in out-of-sample mean squared log error (quarter-aligned) when it is
    left out of the training fit, as % of the test sales' log-price variance; and, given a full fit, the unique share
    of in-sample variation (partial R^2)."""
    out = {}
    periods = [r["period"] for r in test_common]
    base_err, _ = align([p - r["y"] for p, r in zip(predict(train_model, test_common, period_effect=forecast_level(train_model)), test_common)], periods)
    base_mse = mse(base_err)
    yv = [r["y"] for r in test_common]
    ybar = sum(yv) / len(yv) if yv else 0
    tot = sum((y - ybar) ** 2 for y in yv) / len(yv) if yv else None
    for g, tcols in train_model["design"].groups.items():
        oos = partial = None
        if tcols and test_common and g != "Sale quarter":
            bt = restricted(train_model, tcols)
            if bt:
                fake = dict(train_model, beta=bt)
                err, _ = align([p - r["y"] for p, r in zip(predict(fake, test_common, beta=bt, period_effect=forecast_level(fake)), test_common)], periods)
                oos = (mse(err) - base_mse) / tot if tot else None
        if full is not None and full["design"].groups.get(g):
            b = restricted(full, full["design"].groups[g])
            partial = (sse_of(full, b) - full["sse"]) / full["sst"] if b else None
        out[g] = {"partialR2": round(partial * 100, 2) if partial is not None else None,
                  "oos": round(oos * 100, 2) if oos is not None else None}
    return out


def driver_rows(model, spec, imp):
    """One row per driver for the ranked chart: the headline contrast, all levels, coverage and importance."""
    eff = effects(model)
    d = model["design"]
    rows = []
    for g in spec:
        name = g["name"]
        if name not in eff:
            continue
        levels = eff[name]
        head = levels[0] if g["kind"] == "log" else next((lv for lv in levels if lv["level"] == g.get("headline")), None)
        if head is None or head.get("ref"):
            head = max((lv for lv in levels if not lv.get("ref") and not lv.get("missing")), key=lambda lv: abs(lv["pct"]), default=None)
        c = d.counts[name]
        covered = sum(v for k, v in c.items() if k != NOT_RECORDED)
        rows.append({"name": name, "kind": g["kind"], "ref": g.get("ref"), "headline": head, "levels": levels,
                     "coverage": round(covered / max(1, sum(c.values())) * 100, 1), **imp.get(name, {})})
    return rows


def analyse(rows, lookup, cfg, through=None, area_names=None, history=None, log=print, extras=None):
    """Everything model.json holds, plus out-of-sample residuals for the latest year. Pure: no file access.

    Given a dict as `extras`, also puts the features + neighbourhood fit's coefficients there as "predictor"."""
    t0 = time.time()
    records, dropped = records_from(rows, lookup, cfg, history)
    records.sort(key=lambda r: r["date"])
    spec = cfg["spec"]
    n_train = sum(1 for r in records if r["date"] <= TRAIN_END)
    train, test = records[:n_train], records[n_train:]

    # 1. features + neighbourhood (the first model)
    base_train = fit(Design(spec, train), train, robust=False)
    t_near = time.time()
    comps = nearby_sales(records)
    near_seconds = round(time.time() - t_near, 1)
    with_comps = sum(1 for c in comps if len(c) >= NEAR_MIN)
    log(f"  model: base fit, nearby sales for {with_comps:,} of {len(records):,} sales ({near_seconds}s), {time.time() - t0:.0f}s")

    # 2. + nearby sales: comparables' residuals from the training fit (later sales scored at the 2024 level)
    near_tr = nearby_feature(comps, residuals(base_train, records, n_train))
    rec_nb = with_features(records, **{NEARBY["name"]: near_tr})
    spec_nb = spec + [NEARBY]
    nb_train = fit(Design(spec_nb, rec_nb[:n_train]), rec_nb[:n_train], robust=False)

    # 3. assessor's value (certified before the sale's year) + adjustments
    have_prior = any(r["prior"] for r in records)
    b_train = None
    if have_prior:
        prior_log = [math.log(r["prior"]) if r["prior"] else None for r in records]
        rec_b = with_features(rec_nb, **{ASSESSOR["name"]: prior_log})
        spec_b = [ASSESSOR] + spec_nb
        b_train = fit(Design(spec_b, rec_b[:n_train]), rec_b[:n_train], robust=False)
    log(f"  model: variant fits {time.time() - t0:.0f}s")

    # backtest on the test sales every method can predict
    level = forecast_level(base_train)
    ppsf = ppsf_baseline(train, test, f"{base_train['design'].periods[-1][:4]}-01-01")
    idx = [i for i, r in enumerate(test) if ppsf[i] is not None and r["value"] and (r["prior"] or not have_prior)]
    preds = {}
    if have_prior:
        preds["assessor"] = [math.log(r["prior"]) if r["prior"] else None for r in test]
    preds["model"] = predict(base_train, test, period_effect=level)
    preds["nearby"] = predict(nb_train, rec_nb[n_train:], period_effect=forecast_level(nb_train))
    if b_train:
        preds["adjusted"] = predict(b_train, rec_b[n_train:], period_effect=forecast_level(b_train))
    preds["ppsf"] = ppsf
    preds["assessorNow"] = [math.log(r["value"]) if r["value"] else None for r in test]
    bt = backtest(preds, test, idx)
    bt["forecastLevel"] = "average quarter effect of " + ", ".join(base_train["design"].periods[-4:])
    bt["nearby"] = {"k": NEAR_K, "min": NEAR_MIN, "days": NEAR_DAYS, "km": NEAR_KM, "seconds": near_seconds,
                    "share": round(with_comps / len(records) * 100, 1) if records else None}

    # what the assessor misses: drop-one importance inside "assessor's value + adjustments"
    misses = None
    if b_train:
        common_b = [rec_b[n_train + i] for i in idx]
        imp_b = importance(b_train, common_b)
        eff_b = {r["name"]: r for r in driver_rows(b_train, spec_b, imp_b)}
        eff_b["Neighbourhood"] = {"name": "Neighbourhood", "kind": "fe", **imp_b.get("Neighbourhood", {})}
        misses = sorted(({"name": k, "oos": v.get("oos"), "headline": v.get("headline")}
                         for k, v in eff_b.items() if k != ASSESSOR["name"] and v.get("oos") is not None),
                        key=lambda x: -x["oos"])
        misses = {"assessorElasticity": round(b_train["beta"][b_train["design"].cont[ASSESSOR["name"]][0]], 3),
                  "drivers": misses}
    log(f"  model: backtest {time.time() - t0:.0f}s")

    # caveat check: fit on 2023-24 only vs all training years (features + neighbourhood)
    common_nb = [rec_nb[n_train + i] for i in idx]
    common = [test[i] for i in idx]
    recent = [r for r in train if r["date"] >= RECENT_FROM]
    recent_model = fit(Design(spec, recent), recent, robust=False)
    recent_err, _ = align([p - r["y"] for p, r in zip(predict(recent_model, common, period_effect=forecast_level(recent_model)), common)],
                          [r["period"] for r in common])

    # the published effects: features + nearby sales on every sale, nearby residuals from an all-years fit
    base_full = fit(Design(spec, records), records, robust=False)
    if extras is not None:
        extras["predictor"] = predictor_of(base_full, through)
    rec_full =with_features(records, **{NEARBY["name"]: nearby_feature(comps, base_full["resid"])})
    full = fit(Design(spec_nb, rec_full), rec_full, robust=True)
    log(f"  model: full fits {time.time() - t0:.0f}s")
    imp = importance(nb_train, common_nb, full)
    drivers = driver_rows(full, spec_nb, imp)
    premiums, raw_area, mean_area = area_premiums(full, area_names)
    d = full["design"]
    weights = [(raw_area[a] - mean_area, n) for a, n in d.area_n.items() if n >= MIN_AREA]
    q25, q75 = weighted_quantile(weights, 0.25), weighted_quantile(weights, 0.75)
    drivers.append({"name": "Neighbourhood", "kind": "fe", "coverage": 100.0,
                    "headline": {"level": "upper vs lower quartile area", "pct": pct(q75 - q25), "lo": None, "hi": None},
                    "areas": sum(1 for _, n in d.area_n.items() if n >= MIN_AREA), **imp.get("Neighbourhood", {})})
    drivers.sort(key=lambda r: -(r.get("oos") if r.get("oos") is not None else -1))
    te = time_effects(full)

    rehab = defaultdict(list)
    for r, e in zip(records, full["resid"]):
        if r["f"].get("Interior condition") in ("New construction", "Rehabbed"):
            rehab[r["date"][:4]].append(e)
    train_eff, recent_eff = effects(base_train), effects(recent_model)
    model_scores = next(m["aligned"] for m in bt["methods"] if m["id"] == "model")
    current = {
        "rehabResidualByYear": [{"year": y, "n": len(v), "pct": pct(median(v))} for y, v in sorted(rehab.items())],
        "allYears": {"from": train[0]["date"] if train else None, "n": base_train["n"], "backtest": model_scores},
        "recentOnly": {"from": RECENT_FROM, "n": recent_model["n"], "backtest": score(recent_err)},
        "conditions": {g: {"allYears": train_eff.get(g), "recentOnly": recent_eff.get(g)}
                       for g in ("Interior condition", "Exterior condition")},
    }

    # out-of-sample residuals for the latest year: each sale against the backtest fit of features + nearby sales
    # (fitted through TRAIN_END), with each quarter's median miss removed; whole percent to keep the file small
    latest = records[-1]["date"]
    cutoff = (dt.date.fromisoformat(latest) - dt.timedelta(days=365)).isoformat()
    err, _ = align([p - r["y"] for p, r in zip(preds["nearby"], test)], [r["period"] for r in test])
    res = {"id": [], "pct": []}
    for r, e in zip(test, err):
        if r["date"] > cutoff:
            res["id"].append(r["id"])
            res["pct"].append(round((math.exp(-e) - 1) * 100))      # + = sold above what its features predict

    out = {
        "through": through, "latestSale": latest, "seconds": None,
        "sales": len(rows), "modelled": len(records), "dropped": dict(dropped),
        "fit": {"n": full["n"], "trimmed": full["trimmed"], "r2": round(full["r2"], 3), "rmse": round(full["rmse"], 3),
                "columns": d.p, "areas": len(d.area_col) + 1, "quarters": len(d.periods), "se": "HC1 (robust)"},
        "drivers": drivers,
        "coverage": [{"name": dv["name"], "coverage": dv["coverage"]} for dv in drivers if dv["kind"] != "fe"],
        "marketLevel": {"from": d.periods[0], "to": d.periods[-1], "series": [{"q": q, "pct": pct(v)} for q, v in te.items()]},
        "neighbourhoods": premiums,
        "backtest": bt,
        "assessorMisses": misses,
        "assessorHistory": have_prior,
        "currentRecord": current,
        "caveat": cfg.get("caveat"),
        "missing": "A characteristic that is not recorded gets its own 'Not recorded' level; floor and lot area get an "
                   "indicator and the average value. Sales with no neighbourhood or no property record are left out.",
        "outliers": f"Sales more than {TRIM:g} robust SDs from the first fit were trimmed and the model refitted once.",
    }
    out["seconds"] = round(time.time() - t0, 1)
    return out, {"latestSale": latest, "from": cutoff, "basis": f"out of sample: fitted through {TRAIN_END}", **res}


def write_city(store, city, rows, out_dir, through=None, log=print, area_names=None):
    """Write <city>/model.json (and model-residuals.json) when the city has a characteristics lookup; else remove them.

    `area_names` maps area key -> display name (the city's overrides applied); without it the key's name part is shown."""
    from .characteristics import CITIES, lookup_name
    paths = [os.path.join(out_dir, city, f) for f in ("model.json", "model-residuals.json", "model-predictor.json")]
    cfg = CITIES.get(city)
    lookup = store.read_lookup(lookup_name(city)) if cfg else {}
    if not lookup:
        for path in paths:
            if os.path.exists(path):
                os.remove(path)
        return None
    history = store.read_lookup(cfg["history"]) if cfg.get("history") else None
    extras = {}
    result, residuals = analyse(rows, lookup, cfg, through, area_names=area_names, history=history or None, log=log,
                                extras=extras)
    # the coefficients engine/listing_cards.py prices current listings with, and how far off the same model was on
    # later sales (quarter-aligned backtest of "features + neighbourhood"), overall and by price band
    bt = result["backtest"]
    predictor = {"through": through, "latestSale": result["latestSale"], **extras["predictor"],
                 "error": {m["id"]: m["aligned"] for m in bt["methods"] if m["id"] in ("model", "ppsf")},
                 "errorByBand": [{"label": b["label"], "model": b.get("model"), "ppsf": b.get("ppsf")} for b in bt["byBand"]],
                 "bands": [[lo, hi, label] for lo, hi, label in PRICE_BANDS]}
    os.makedirs(os.path.join(out_dir, city), exist_ok=True)
    for path, obj in zip(paths, (result, residuals, predictor)):
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"city": city, **obj}, f, separators=(",", ":"))
    log(f"  model: {result['modelled']:,} sales, {result['seconds']}s")
    return result
