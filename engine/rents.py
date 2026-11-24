"""One rent picture per area, from sources that work in tandem.

Two kinds of figure measure different things, and each is kept with its date and basis:

- **Asking rent** (Zillow Observed Rent Index, by ZIP): what new leases are advertised at, this month.
- **Rent paid** (Census Bureau, American Community Survey 5-year, by tract): median gross rent (rent plus utilities)
  that renter households report paying, over a five-year survey period, stated in the final year's dollars.

The survey figure is "brought to today" by the area's own asking-rent change since that dollar year (the ACS has
already adjusted the five years to the final year's dollars, so the change is counted from that year, not from the
survey midpoint). The headline is the midpoint of the two current figures; the two ends give a range. Pure functions
only: analytics.add_context reads the lookups and calls these.
"""
from collections import Counter, defaultdict

RELIABLE_MOE = 0.25        # a Census figure whose 90% margin of error exceeds a quarter of the estimate is not used
MIN_RENTERS = 300          # fewer renter homes than this in the catchment: too thin to stand for the area
MIN_COVERAGE = 0.6         # share of the catchment's renter homes in tracts with a published median
MAX_OPEN = 0.25            # share of renter weight in tracts whose median is top- or bottom-coded ($3,500+ / under $100)
DISAGREE = 0.25            # the two current figures differ by more than this share of the headline: "sources disagree"
ASK_MIN_COVERAGE = 0.5     # as for the asking rent itself: share of the area's sales in ZIPs with a ZORI figure
ROLL_MIN_COVERAGE = 0.8    # share of that weight whose ZIPs also have the dollar year's average


def tract_shares(groups, nearest):
    """{area key: {tract GEOID: share}}: the share of each tract's 2020 residents whose block group lies in the area's
    catchment (the same nearest-centre catchments used for residents and incidents)."""
    tract_pop, in_area = Counter(), defaultdict(Counter)
    for geoid, g in groups.items():
        pop = int(g["pop"])
        tract_pop[geoid[:11]] += pop
        key = nearest((float(g["lat"]), float(g["lon"])))
        if key:
            in_area[key][geoid[:11]] += pop
    return {k: {t: n / tract_pop[t] for t, n in c.items()} for k, c in in_area.items()}


def _n(v):
    return float(v) if v not in (None, "") else None


def _combine(pool):
    """Renter-weighted mean of tract medians with an approximate 90% margin: sqrt(sum((w/W)^2 * moe^2)), treating the
    tract estimates as independent and the weights as fixed (the Census Bureau's approximation for derived figures).
    `pool` is [(weight, estimate or None, moe or None)]. Returns (estimate, moe, coverage) or None."""
    total = sum(w for w, _, _ in pool)
    have = [(w, v, m) for w, v, m in pool if v is not None]
    wv = sum(w for w, _, _ in have)
    if not total or not wv:
        return None
    est = sum(w * v for w, v, _ in have) / wv
    moe = sum((w / wv) ** 2 * m ** 2 for w, _, m in have if m is not None) ** 0.5
    return est, moe, wv / total


def paid_rent(shares, tracts):
    """The Census rent for one catchment: {value, moe, renters, reliable, year, period, beds} or None.

    Each tract counts with its renter-occupied homes (B25003) times the share of it in the catchment. A combined
    median is approximated by the weighted mean of tract medians. Bedrooms use the same weights (the counts by
    bedroom are not fetched), so they are rougher; a bedroom figure is kept only where it passes the same tests.
    """
    pool = []
    for t, s in shares.items():
        rec = tracts.get(t)
        if rec and _n(rec.get("renters")):
            pool.append((s * _n(rec["renters"]), rec))
    if not pool:
        return None
    renters = sum(w for w, _ in pool)
    got = _combine([(w, _n(r["rent"]), _n(r["rent_moe"])) for w, r in pool])
    if not got:
        return None
    est, moe, coverage = got
    open_share = sum(w for w, r in pool if r.get("open") and _n(r["rent"]) is not None) / (renters * coverage)
    year = int(next(r["year"] for _, r in pool if r.get("year")))
    reliable = renters >= MIN_RENTERS and coverage >= MIN_COVERAGE and open_share <= MAX_OPEN and moe / est <= RELIABLE_MOE
    beds = {}
    for i in range(6):
        b = _combine([(w, _n(r.get(f"b{i}")), _n(r.get(f"b{i}_moe"))) for w, r in pool])
        if b and b[2] >= MIN_COVERAGE and b[1] / b[0] <= RELIABLE_MOE:
            beds[str(i)] = round(b[0])
    return {"value": round(est), "moe": round(moe), "renters": round(renters), "coverage": round(coverage * 100),
            "openShare": round(open_share * 100), "reliable": reliable, "year": year, "period": f"{year - 4}–{year}",
            "beds": beds or None}


def hud_rents(zips, safmr):
    """HUD Small Area FMRs for an area by bedrooms, weighted by the area's sales per ZIP: {year, beds} or None.

    A fair market rent is a 40th percentile of what recent movers pay, so it sits below a typical new-lease asking
    rent and is not a median like the other two figures. It is shown beside them as a cross-check and gives the
    by-bedroom line, but it does not move the headline: averaging a 40th percentile for one home size into medians
    for all sizes would pull each area's headline by its housing mix rather than by its rents.
    """
    total = sum(zips.values())
    have = {z: n for z, n in zips.items() if z in safmr}
    if not total or sum(have.values()) / total < ASK_MIN_COVERAGE:
        return None
    beds = {}
    for i in range(5):
        pts = [(float(safmr[z][f"b{i}"]), n) for z, n in have.items() if safmr[z].get(f"b{i}") not in (None, "")]
        if pts and sum(n for _, n in pts) / total >= ASK_MIN_COVERAGE:
            beds[str(i)] = round(sum(v * n for v, n in pts) / sum(n for _, n in pts) / 10) * 10
    year = max((safmr[z].get("year") or "" for z in have), default="")
    return {"year": year or None, "beds": beds} if beds else None


def annual(text):
    """The "2023:1612;2024:1655" column of the ZORI lookup as {year: average}."""
    out = {}
    for part in (text or "").split(";"):
        if ":" in part:
            y, v = part.split(":")
            out[int(y)] = float(v)
    return out


def roll_factor(zips, zori, year):
    """Asking-rent change from the average of `year` to the latest month, over the area's ZIPs weighted by its sales.
    None unless ZIPs holding ROLL_MIN_COVERAGE of the ZORI-covered weight have that year's average."""
    have = {z: n for z, n in zips.items() if z in zori}
    w = sum(have.values())
    if not w or w / (sum(zips.values()) or 1) < ASK_MIN_COVERAGE:
        return None
    both = {z: n for z, n in have.items() if year in annual(zori[z].get("annual"))}
    if sum(both.values()) / w < ROLL_MIN_COVERAGE:
        return None
    now = sum(int(zori[z]["rent"]) * n for z, n in both.items())
    then = sum(annual(zori[z]["annual"])[year] * n for z, n in both.items())
    return now / then if then else None


def blend(asking, paid, factor):
    """The headline rent and its range from whichever figures exist.

    Returns {rent, low, high, basis, disagree, paidToday}: basis "blend" (midpoint of paid-brought-to-today and asking),
    "asking" (Zillow only), "paid" (Census brought to today only) or "survey" (Census survey figure, not brought
    forward, used only when there is no asking rent either). A Census figure that fails the reliability tests is
    reported but left out of the headline.
    """
    today = round(paid["value"] * factor) if paid and paid["reliable"] and factor else None
    if today and asking:
        lo, hi = sorted((today, asking))
        mid = round((today + asking) / 2)
        return {"rent": mid, "low": lo, "high": hi, "basis": "blend", "disagree": (hi - lo) / mid > DISAGREE, "paidToday": today}
    if asking:
        return {"rent": asking, "low": asking, "high": asking, "basis": "asking", "disagree": False, "paidToday": None}
    if today:
        return {"rent": today, "low": today, "high": today, "basis": "paid", "disagree": False, "paidToday": today}
    if paid and paid["reliable"]:
        v = paid["value"]
        return {"rent": v, "low": v, "high": v, "basis": "survey", "disagree": False, "paidToday": None}
    return None
