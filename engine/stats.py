"""Small, pure statistical helpers used by the analytics build. Standard library only."""
import math

EXACT_LIMIT = 1000   # above this many events or sales, normal approximations are accurate and much cheaper


def _binom_cdf_half(k, n):
    """P(X <= k) for X ~ Binomial(n, 0.5), exact."""
    if k < 0:
        return 0.0
    if k >= n:
        return 1.0
    return sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n


def poisson_two_count_p(a, b):
    """Two-sided p-value for H0: two Poisson counts a and b share one rate (equal exposure).

    Conditional on the total n = a + b, a is Binomial(n, 0.5) under H0. Exact for small totals,
    normal approximation with continuity correction for large ones.
    """
    n = a + b
    if n == 0:
        return 1.0
    k = min(a, b)
    if n <= EXACT_LIMIT:
        return min(1.0, 2 * _binom_cdf_half(k, n))
    z = (abs(a - n / 2) - 0.5) / math.sqrt(n / 4)
    return min(1.0, math.erfc(max(0.0, z) / math.sqrt(2)))


def count_trend(cur, prev, min_total=20, alpha=0.05):
    """('up' | 'down' | 'flat', percent change or None). A change is published only when it is
    unlikely to be chance: enough events and p < alpha on the two-count test."""
    total = cur + prev
    if total < min_total or prev == 0 or poisson_two_count_p(cur, prev) >= alpha:
        return "flat", None
    change = round((cur / prev - 1) * 100, 1)
    return ("up" if cur > prev else "down"), change


def eb_rates(counts, pops):
    """Global empirical-Bayes rates (Marshall 1991): each area's raw rate is pulled toward the
    overall rate, more strongly the smaller its population.

    Returns (shrunk rates per person, overall rate m, between-area variance s2, weights).
    """
    pairs = [(o, p) for o, p in zip(counts, pops) if p > 0]
    if not pairs:
        return [None] * len(counts), None, None, [None] * len(counts)
    total_o = sum(o for o, _ in pairs)
    total_p = sum(p for _, p in pairs)
    m = total_o / total_p
    p_bar = total_p / len(pairs)
    s2 = sum(p * (o / p - m) ** 2 for o, p in pairs) / total_p - m / p_bar
    s2 = max(0.0, s2)
    out, weights = [], []
    for o, p in zip(counts, pops):
        if p <= 0:
            out.append(None)
            weights.append(None)
            continue
        w = s2 / (s2 + m / p) if s2 + m / p > 0 else 0.0
        out.append(w * (o / p) + (1 - w) * m)
        weights.append(w)
    return out, m, s2, weights


def median_ci_ranks(n, conf=0.95):
    """1-based ranks (l, u) so that [x_(l), x_(u)] covers the population median with probability
    >= conf, distribution-free. None when n is too small for any such interval (n < 6 at 95%)."""
    if n < 1:
        return None
    if n <= EXACT_LIMIT:
        best = None
        tail = 0.0
        for l in range(1, n // 2 + 1):
            tail += math.comb(n, l - 1) / 2 ** n       # P(X <= l - 1)
            if 1 - 2 * tail >= conf:
                best = l
            else:
                break
        return (best, n + 1 - best) if best else None
    z = 1.959963984540054 if conf == 0.95 else _z(conf)
    l = int(math.floor((n - z * math.sqrt(n)) / 2))
    return max(1, l), min(n, n + 1 - max(1, l))


def _z(conf):
    lo, hi = 0.0, 10.0
    for _ in range(80):
        mid = (lo + hi) / 2
        if math.erf(mid / math.sqrt(2)) < conf:
            lo = mid
        else:
            hi = mid
    return hi


def median_ci(sorted_values, conf=0.95):
    """(low, high) distribution-free interval for the median of already sorted values, or (None, None)."""
    ranks = median_ci_ranks(len(sorted_values), conf)
    if not ranks:
        return None, None
    return sorted_values[ranks[0] - 1], sorted_values[ranks[1] - 1]


def solve(a, b):
    """Solve a x = b by Gaussian elimination with partial pivoting. None if singular."""
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(m[r][col]))
        if abs(m[piv][col]) < 1e-9:
            return None
        m[col], m[piv] = m[piv], m[col]
        for r in range(col + 1, n):
            f = m[r][col] / m[col][col]
            if f:
                for c in range(col, n + 1):
                    m[r][c] -= f * m[col][c]
    x = [0.0] * n
    for r in range(n - 1, -1, -1):
        x[r] = (m[r][n] - sum(m[r][c] * x[c] for c in range(r + 1, n))) / m[r][r]
    return x


def repeat_sales_index(pairs, periods):
    """Bailey-Muth-Nourse repeat-sales index.

    pairs: (first_period, second_period, log(price2 / price1)) with periods 0..periods-1, oldest first.
    Regresses log price ratios on dummies (+1 at the second sale's period, -1 at the first's) by
    ordinary least squares, solved through the normal equations. The oldest period with any sales is
    the base (log index 0). Returns (index levels with base = 1.0, half-pairs per period); a level is
    None for periods with no sales or when the system cannot be solved.
    """
    half = [0] * periods
    for t1, t2, _ in pairs:
        half[t1] += 1
        half[t2] += 1
    used = [t for t in range(periods) if half[t]]
    if len(used) < 2:
        return [None] * periods, half
    base, free = used[0], used[1:]
    col = {t: i for i, t in enumerate(free)}
    k = len(free)
    xtx = [[0.0] * k for _ in range(k)]
    xty = [0.0] * k
    for t1, t2, y in pairs:
        i, j = col.get(t2), col.get(t1)     # base period has no column
        if i is not None:
            xtx[i][i] += 1
            xty[i] += y
        if j is not None:
            xtx[j][j] += 1
            xty[j] -= y
        if i is not None and j is not None:
            xtx[i][j] -= 1
            xtx[j][i] -= 1
    beta = solve(xtx, xty)
    if beta is None:
        return [None] * periods, half
    levels = [None] * periods
    levels[base] = 1.0
    for t, i in col.items():
        levels[t] = math.exp(beta[i])
    return levels, half
