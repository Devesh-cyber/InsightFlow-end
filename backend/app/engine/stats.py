"""
Small statistics helpers that would otherwise need SciPy (a ~40 MB dependency).

chi2_sf: survival function of the chi-square distribution = the p-value of a
chi-square test. Uses the regularized upper incomplete gamma function
(series for small x, continued fraction for large x; Numerical Recipes §6.2).
Verified against SciPy reference values in tests/test_stats.py.
"""

import math

_EPS = 1e-14
_MAX_ITER = 500


def _gamma_p_series(a: float, x: float) -> float:
    total = term = 1.0 / a
    ap = a
    for _ in range(_MAX_ITER):
        ap += 1
        term *= x / ap
        total += term
        if abs(term) < abs(total) * _EPS:
            break
    return total * math.exp(-x + a * math.log(x) - math.lgamma(a))


def _gamma_q_cont_frac(a: float, x: float) -> float:
    tiny = 1e-300
    b = x + 1 - a
    c = 1 / tiny
    d = 1 / b
    h = d
    for i in range(1, _MAX_ITER):
        an = -i * (i - a)
        b += 2
        d = an * d + b
        d = tiny if abs(d) < tiny else d
        c = b + an / c
        c = tiny if abs(c) < tiny else c
        d = 1 / d
        delta = d * c
        h *= delta
        if abs(delta - 1) < _EPS:
            break
    return math.exp(-x + a * math.log(x) - math.lgamma(a)) * h


def chi2_sf(statistic: float, dof: int) -> float:
    if dof <= 0 or statistic < 0 or math.isnan(statistic):
        return float("nan")
    if statistic == 0:
        return 1.0
    a, x = dof / 2.0, statistic / 2.0
    if x < a + 1:
        return max(0.0, min(1.0, 1.0 - _gamma_p_series(a, x)))
    return max(0.0, min(1.0, _gamma_q_cont_frac(a, x)))


def correlation_strength(r: float) -> str:
    r = abs(r)
    if r >= 0.7:
        return "strong"
    if r >= 0.4:
        return "moderate"
    if r >= 0.2:
        return "weak"
    return "very weak or none"


def association_strength(v: float) -> str:
    if v >= 0.5:
        return "strong"
    if v >= 0.3:
        return "moderate"
    if v >= 0.1:
        return "weak"
    return "very weak or none"
