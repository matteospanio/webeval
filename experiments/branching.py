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
            return ans == target
        if op == "ne":
            return ans != target
        if op == "in":
            return ans in (target or [])
        if op == "nin":
            return ans not in (target or [])
        if op == "contains":
            if isinstance(ans, (list, str, dict)):
                return target in ans
            return False
        if op in {"gt", "lt", "gte", "lte"}:
            a, b = float(ans), float(target)
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
