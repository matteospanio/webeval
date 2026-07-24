"""Ready-to-use inferential tests comparing conditions, with no scipy dependency.

scipy lives in an optional ``analysis`` extra that isn't installed in the
default (CI/test) environment, so the p-values here are computed from
hand-rolled special functions (regularized incomplete beta + gamma, the
standard Numerical-Recipes formulations). That keeps "ready-to-use
statistical tests" working everywhere PANEL runs.

* numeric / rating / likert outcomes across conditions -> one-way ANOVA
  (F-test; for two conditions this equals the pooled t-test).
* choice outcomes across conditions -> chi-square test of independence.

Only per-stimulus questions can be compared across conditions (demographic /
screening answers aren't tied to a condition).
"""
from __future__ import annotations

import json
import math
from collections import Counter
from statistics import mean

from experiments.models import Experiment, Question
from experiments.queries import real_responses

_TINY = 1e-300
_EPS = 1e-14


# --- special functions ------------------------------------------------------


def _betacf(a: float, b: float, x: float) -> float:
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < _TINY:
        d = _TINY
    d = 1.0 / d
    h = d
    for m in range(1, 300):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < _TINY:
            d = _TINY
        c = 1.0 + aa / c
        if abs(c) < _TINY:
            c = _TINY
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < _TINY:
            d = _TINY
        c = 1.0 + aa / c
        if abs(c) < _TINY:
            c = _TINY
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < _EPS:
            break
    return h


def betainc(a: float, b: float, x: float) -> float:
    """Regularized incomplete beta function I_x(a, b)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    lbeta = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
    front = math.exp(lbeta + a * math.log(x) + b * math.log(1.0 - x))
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x) / a
    return 1.0 - front * _betacf(b, a, 1.0 - x) / b


def _gammq_cf(a: float, x: float) -> float:
    b = x + 1.0 - a
    c = 1.0 / _TINY
    d = 1.0 / b
    h = d
    for i in range(1, 300):
        an = -i * (i - a)
        b += 2.0
        d = an * d + b
        if abs(d) < _TINY:
            d = _TINY
        c = b + an / c
        if abs(c) < _TINY:
            c = _TINY
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < _EPS:
            break
    return math.exp(-x + a * math.log(x) - math.lgamma(a)) * h


def _gammp(a: float, x: float) -> float:
    """Regularized lower incomplete gamma P(a, x)."""
    if x <= 0.0:
        return 0.0
    if x < a + 1.0:
        ap = a
        total = 1.0 / a
        delta = total
        for _ in range(1000):
            ap += 1.0
            delta *= x / ap
            total += delta
            if abs(delta) < abs(total) * _EPS:
                break
        return total * math.exp(-x + a * math.log(x) - math.lgamma(a))
    return 1.0 - _gammq_cf(a, x)


def t_sf_two_sided(t: float, df: float) -> float:
    """Two-sided p-value P(|T| > |t|) for Student's t with ``df`` d.o.f."""
    if df <= 0:
        return float("nan")
    return betainc(df / 2.0, 0.5, df / (df + t * t))


def f_sf(f: float, df1: float, df2: float) -> float:
    """Upper-tail p-value P(F > f)."""
    if f <= 0:
        return 1.0
    return betainc(df2 / 2.0, df1 / 2.0, df2 / (df2 + df1 * f))


def chi2_sf(x: float, df: float) -> float:
    """Upper-tail p-value P(chi^2 > x)."""
    if x <= 0:
        return 1.0
    return 1.0 - _gammp(df / 2.0, x / 2.0)


# --- tests ------------------------------------------------------------------


def one_way_anova(groups: list[list[float]]) -> dict | None:
    groups = [g for g in groups if g]
    k = len(groups)
    n = sum(len(g) for g in groups)
    if k < 2 or n - k < 1:
        return None
    grand = sum(sum(g) for g in groups) / n
    ss_between = sum(len(g) * (mean(g) - grand) ** 2 for g in groups)
    ss_within = sum(sum((x - mean(g)) ** 2 for x in g) for g in groups)
    df1, df2 = k - 1, n - k
    if ss_within <= 0:
        f = float("inf") if ss_between > 0 else 0.0
        p = 0.0 if ss_between > 0 else 1.0
    else:
        f = (ss_between / df1) / (ss_within / df2)
        p = f_sf(f, df1, df2)
    return {"test": "One-way ANOVA", "statistic": f, "df": f"{df1}, {df2}",
            "p_value": p}


def chi_square_test(observed: list[list[int]]) -> dict | None:
    rows = len(observed)
    cols = len(observed[0]) if rows else 0
    if rows < 2 or cols < 2:
        return None
    row_tot = [sum(r) for r in observed]
    col_tot = [sum(observed[i][j] for i in range(rows)) for j in range(cols)]
    total = sum(row_tot)
    if total == 0:
        return None
    chi = 0.0
    for i in range(rows):
        for j in range(cols):
            expected = row_tot[i] * col_tot[j] / total
            if expected > 0:
                chi += (observed[i][j] - expected) ** 2 / expected
    df = (rows - 1) * (cols - 1)
    return {"test": "Chi-square", "statistic": chi, "df": str(df),
            "p_value": chi2_sf(chi, df)}


def _grouped_answers(experiment: Experiment, question: Question) -> dict[str, list]:
    rows = real_responses(
        experiment, question=question, stimulus__isnull=False
    ).values_list("stimulus__condition__name", "answer_value")
    groups: dict[str, list] = {}
    for cond, raw in rows:
        try:
            groups.setdefault(cond, []).append(json.loads(raw))
        except (TypeError, ValueError):
            continue
    return groups


def compare_conditions(experiment: Experiment, question: Question) -> dict:
    """Run the appropriate condition comparison for a per-stimulus question."""
    if question.section != Question.Section.STIMULUS:
        return {"applicable": False,
                "reason": "Only per-stimulus questions compare across conditions."}
    groups = _grouped_answers(experiment, question)
    if len(groups) < 2:
        return {"applicable": False,
                "reason": "Need at least two conditions with responses."}

    t = question.type
    if t in (Question.Type.RATING, Question.Type.NUMERIC, Question.Type.LIKERT):
        numeric_groups, summary = [], []
        for cond, vals in groups.items():
            nums = []
            for v in vals:
                try:
                    nums.append(float(v))
                except (TypeError, ValueError):
                    continue
            if nums:
                numeric_groups.append(nums)
                summary.append({"condition": cond, "n": len(nums), "mean": mean(nums)})
        result = one_way_anova(numeric_groups)
        if result is None:
            return {"applicable": False, "reason": "Not enough data for a test."}
        result.update(applicable=True, groups=summary)
        return result

    if t == Question.Type.CHOICE:
        options: list[str] = []
        for vals in groups.values():
            for v in vals:
                for x in (v if isinstance(v, list) else [v]):
                    if str(x) not in options:
                        options.append(str(x))
        conditions = list(groups)
        observed = []
        for cond in conditions:
            counter: Counter = Counter()
            for v in groups[cond]:
                for x in (v if isinstance(v, list) else [v]):
                    counter[str(x)] += 1
            observed.append([counter.get(opt, 0) for opt in options])
        result = chi_square_test(observed)
        if result is None:
            return {"applicable": False, "reason": "Not enough data for a test."}
        result.update(applicable=True,
                      groups=[{"condition": c} for c in conditions])
        return result

    return {"applicable": False,
            "reason": f"No built-in condition test for '{t}' questions."}
