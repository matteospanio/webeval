"""Power / sample-size analysis for a two-group comparison.

Pure math (stdlib only): the standard normal CDF (via ``math.erf``) and its
inverse (Acklam's rational approximation), used for the two-sample t-test
sample-size and power formulas under the usual normal approximation. Effect
size is Cohen's d; estimating d from pilot data lets researchers size a
follow-up study from a small run.
"""
from __future__ import annotations

import math
from statistics import mean, variance

# Acklam's inverse-normal-CDF coefficients.
_A = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
      1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
_B = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
      6.680131188771972e+01, -1.328068155288572e+01]
_C = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
      -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
_D = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
      3.754408661907416e+00]


def norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def norm_ppf(p: float) -> float:
    """Inverse standard-normal CDF (quantile) for 0 < p < 1."""
    if not 0.0 < p < 1.0:
        raise ValueError("p must be in (0, 1)")
    plow, phigh = 0.02425, 1 - 0.02425
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (((((_C[0] * q + _C[1]) * q + _C[2]) * q + _C[3]) * q + _C[4]) * q + _C[5]) / \
               ((((_D[0] * q + _D[1]) * q + _D[2]) * q + _D[3]) * q + 1)
    if p <= phigh:
        q = p - 0.5
        r = q * q
        return (((((_A[0] * r + _A[1]) * r + _A[2]) * r + _A[3]) * r + _A[4]) * r + _A[5]) * q / \
               (((((_B[0] * r + _B[1]) * r + _B[2]) * r + _B[3]) * r + _B[4]) * r + 1)
    q = math.sqrt(-2 * math.log(1 - p))
    return -(((((_C[0] * q + _C[1]) * q + _C[2]) * q + _C[3]) * q + _C[4]) * q + _C[5]) / \
           ((((_D[0] * q + _D[1]) * q + _D[2]) * q + _D[3]) * q + 1)


def required_n_per_group(effect_size: float, alpha: float = 0.05,
                         power: float = 0.8) -> int | None:
    """Sample size **per group** for a two-sided two-sample t-test."""
    if effect_size <= 0 or not 0 < alpha < 1 or not 0 < power < 1:
        return None
    za = norm_ppf(1 - alpha / 2)
    zb = norm_ppf(power)
    return math.ceil(2 * (za + zb) ** 2 / effect_size ** 2)


def achieved_power(effect_size: float, n_per_group: int,
                   alpha: float = 0.05) -> float:
    """Power of a two-sided two-sample t-test at the given per-group n."""
    if effect_size <= 0 or n_per_group <= 0:
        return 0.0
    za = norm_ppf(1 - alpha / 2)
    return norm_cdf(effect_size * math.sqrt(n_per_group / 2.0) - za)


def cohens_d(group_a: list[float], group_b: list[float]) -> float | None:
    """Cohen's d (pooled SD) between two numeric groups, or None if undefined."""
    n1, n2 = len(group_a), len(group_b)
    if n1 < 2 or n2 < 2:
        return None
    pooled = ((n1 - 1) * variance(group_a) + (n2 - 1) * variance(group_b)) / (n1 + n2 - 2)
    if pooled <= 0:
        return None
    return (mean(group_a) - mean(group_b)) / math.sqrt(pooled)
