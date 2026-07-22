"""Conditional display ("skip logic") for questions.

A :class:`~experiments.models.Question` may carry a ``visible_if`` rule that
hides it unless answers to *earlier* questions in the same section match. Rules
are evaluated server-side against the answers already collected for the current
stimulus (per-stimulus for stimulus questions) or for the demographic section,
so branching is deterministic and works without JavaScript when the controlling
question is on an earlier page. A small progressive-enhancement script
(``survey/js/branching.js``) reveals same-page dependents live.

Rule shapes (all JSON):

* ``{"question": <id>, "op": "eq", "value": "Yes"}`` — a single clause,
* ``{"all": [clause, ...]}`` — every clause must be true,
* ``{"any": [clause, ...]}`` — at least one clause must be true.

Operators: ``eq, ne, in, nin, gt, lt, gte, lte, contains, answered,
not_answered``. This module is pure (no Django/model imports) so both
``experiments.models`` validation and ``survey`` flow code can use it.
"""
from __future__ import annotations

import re
from typing import Any, Iterable

OPERATORS = {
    "eq",
    "ne",
    "in",
    "nin",
    "gt",
    "lt",
    "gte",
    "lte",
    "contains",
    "answered",
    "not_answered",
}

# Operators that do not take a ``value``.
VALUELESS_OPS = {"answered", "not_answered"}


def _is_answered(value: Any) -> bool:
    return value is not None and value != "" and value != [] and value != {}


# The numeric-literal grammar BOTH engines share: plain decimal / scientific
# notation only. Python's float() additionally accepts "inf"/"nan"/"1_0" and
# JS's Number() accepts "0x10" — restricting both to this regex keeps their
# comparisons identical by construction.
_NUMERIC_RE = re.compile(r"^[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$")


def _to_number(x: Any) -> float | None:
    """Parse ``x`` under the shared numeric grammar (None = not numeric)."""
    if isinstance(x, bool):
        return None
    if isinstance(x, (int, float)):
        return float(x)
    if isinstance(x, str) and _NUMERIC_RE.match(x.strip()):
        return float(x)
    return None


def _to_str(x: Any) -> str:
    # Mirror JS String(): booleans stringify lowercase.
    if isinstance(x, bool):
        return "true" if x else "false"
    return str(x)


def _loose_eq(a: Any, b: Any) -> bool:
    """Type-tolerant equality shared with the client-side mirror.

    A rating stored as the int ``3`` matches a rule value authored as ``"3"``
    (and vice versa): numbers compare numerically whenever both sides parse
    under the shared grammar, everything else falls back to string equality.
    Lists compare element-wise loose (checkbox answers are strings in the DOM
    but may be typed in an authored rule). The same semantics live in
    ``survey/js/branching.js`` (looseEq) — change the two together.
    """
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_loose_eq(x, y) for x, y in zip(a, b))
    if isinstance(a, (list, dict)) or isinstance(b, (list, dict)):
        return a == b
    if a is None or b is None:
        return a == b
    na, nb = _to_number(a), _to_number(b)
    if na is not None and nb is not None:
        return na == nb
    return _to_str(a) == _to_str(b)


def _membership(ans: Any, target: Any) -> bool:
    """``in``/``nin`` membership: lists test loose element equality, a string
    target tests substring membership of a *scalar* answer (an array answer is
    never "in" a string — mirrors the JS guard), anything else is an empty
    collection."""
    if isinstance(target, list):
        return any(_loose_eq(ans, item) for item in target)
    if isinstance(target, str):
        if isinstance(ans, (list, dict)):
            return False
        return _to_str(ans) in target
    return False


def _eval_clause(clause: dict, answers: dict[int, Any]) -> bool:
    qid = clause.get("question")
    op = clause.get("op")
    target = clause.get("value")
    ans = answers.get(qid)
    present = qid in answers

    if op == "answered":
        return present and _is_answered(ans)
    if op == "not_answered":
        return not (present and _is_answered(ans))
    if not present:
        return False

    try:
        if op == "eq":
            return _loose_eq(ans, target)
        if op == "ne":
            return not _loose_eq(ans, target)
        if op == "in":
            return _membership(ans, target)
        if op == "nin":
            return not _membership(ans, target)
        if op == "contains":
            if isinstance(ans, list):
                return any(_loose_eq(item, target) for item in ans)
            if isinstance(ans, dict):
                return target in ans
            # Scalar answers coerce to string on both sides, so a rating
            # stored as int 35 still "contains" the value "5".
            return _to_str(target) in _to_str(ans)
        if op in {"gt", "lt", "gte", "lte"}:
            a, b = _to_number(ans), _to_number(target)
            if a is None or b is None:
                return False
            return {
                "gt": a > b,
                "lt": a < b,
                "gte": a >= b,
                "lte": a <= b,
            }[op]
    except (TypeError, ValueError):
        return False
    return False


def iter_clauses(condition: dict) -> Iterable[dict]:
    """Yield the individual clauses of a (possibly compound) condition."""
    if not condition:
        return
    if "all" in condition:
        yield from (condition.get("all") or [])
    elif "any" in condition:
        yield from (condition.get("any") or [])
    else:
        yield condition


def referenced_question_ids(condition: dict) -> list[int]:
    return [
        c.get("question")
        for c in iter_clauses(condition)
        if isinstance(c, dict) and isinstance(c.get("question"), int)
    ]


def evaluate_condition(condition: dict, answers: dict[int, Any]) -> bool:
    if not condition:
        return True
    if "all" in condition:
        return all(_eval_clause(c, answers) for c in (condition.get("all") or []))
    if "any" in condition:
        return any(_eval_clause(c, answers) for c in (condition.get("any") or []))
    return _eval_clause(condition, answers)


def is_visible(question, answers: dict[int, Any]) -> bool:
    """True if ``question`` should be shown given ``answers`` (qid → value)."""
    cond = getattr(question, "visible_if", None)
    if not cond:
        return True
    return evaluate_condition(cond, answers)
