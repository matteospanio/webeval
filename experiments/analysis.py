"""Per-question descriptive analytics for every question type.

webeval's older analytics were rating-centric (``per_stimulus_mean_ratings``)
plus pairwise Bradley-Terry. This module gives **every** question type an
aggregate summary suitable for online viewing and CSV-free reporting: choice
distributions, Likert distributions + means, numeric/rating summary stats,
matrix per-row breakdowns, ranking mean-ranks, and a response count for
free-text and plugin types.

Pure stdlib (+ the answers already stored as JSON) so it runs in the default
environment; inferential tests live in :mod:`experiments.stats_tests`.

Only **real** answers count: submitted, non-preview sessions (matching the
filters in :mod:`experiments.stats` / :mod:`experiments.csv_exports`).
"""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from statistics import mean, median, pstdev
from typing import Any

from experiments.models import Experiment, Question
from survey.models import Response


@dataclass
class QuestionResult:
    question_id: int
    prompt: str
    type: str
    section: str
    n: int
    kind: str  # distribution | numeric | matrix | ranking | text | other
    rows: list[dict] = field(default_factory=list)
    stats: dict | None = None


def _answer_values(experiment: Experiment, question: Question) -> list[Any]:
    """Decoded answers for a question across real (submitted, non-preview) sessions."""
    raws = Response.objects.filter(
        question=question,
        session__experiment=experiment,
        session__submitted_at__isnull=False,
        session__is_preview=False,
    ).values_list("answer_value", flat=True)
    values: list[Any] = []
    for raw in raws:
        try:
            values.append(json.loads(raw))
        except (TypeError, ValueError):
            continue
    return values


def _numeric_stats(values: list[Any]) -> dict | None:
    nums: list[float] = []
    for v in values:
        try:
            nums.append(float(v))
        except (TypeError, ValueError):
            continue
    if not nums:
        return None
    return {
        "n": len(nums),
        "mean": mean(nums),
        "median": median(nums),
        "sd": pstdev(nums) if len(nums) > 1 else 0.0,
        "min": min(nums),
        "max": max(nums),
    }


def _distribution_rows(counts: Counter, labels: list[str], total: int) -> list[dict]:
    """Ordered rows for a categorical distribution: declared labels first, then
    any unexpected values that still showed up."""
    ordered = list(labels) + [v for v in counts if v not in labels]
    rows = []
    for label in ordered:
        c = counts.get(label, 0)
        rows.append(
            {"label": label, "count": c, "pct": (100.0 * c / total) if total else 0.0}
        )
    return rows


def analyse_question(experiment: Experiment, question: Question) -> QuestionResult:
    values = _answer_values(experiment, question)
    cfg = question.config or {}
    t = question.type
    base = dict(
        question_id=question.pk,
        prompt=question.prompt,
        type=t,
        section=question.section,
        n=len(values),
    )

    if t == Question.Type.CHOICE:
        counts: Counter = Counter()
        for v in values:
            if isinstance(v, list):
                counts.update(str(x) for x in v)
            else:
                counts[str(v)] += 1
        labels = [str(c) for c in (cfg.get("choices") or [])]
        # For multi-select, totals exceed respondents; percentages are of n.
        return QuestionResult(
            kind="distribution",
            rows=_distribution_rows(counts, labels, len(values)),
            **base,
        )

    if t == Question.Type.LIKERT:
        labels = [str(lb) for lb in (cfg.get("labels") or [])]
        counts = Counter()
        ints: list[int] = []
        for v in values:
            try:
                idx = int(v)
            except (TypeError, ValueError):
                continue
            ints.append(idx)
            label = labels[idx] if 0 <= idx < len(labels) else str(idx)
            counts[label] += 1
        result = QuestionResult(
            kind="distribution",
            rows=_distribution_rows(counts, labels, len(ints)),
            stats=_numeric_stats(ints),
            **base,
        )
        return result

    if t in (Question.Type.RATING, Question.Type.NUMERIC):
        return QuestionResult(kind="numeric", stats=_numeric_stats(values), **base)

    if t == Question.Type.MATRIX:
        rows_cfg = [str(r) for r in (cfg.get("rows") or [])]
        cols_cfg = [str(c) for c in (cfg.get("columns") or [])]
        per_row: dict[str, Counter] = {r: Counter() for r in rows_cfg}
        for v in values:
            if not isinstance(v, dict):
                continue
            for row, col in v.items():
                per_row.setdefault(str(row), Counter())[str(col)] += 1
        rows = []
        for row in rows_cfg:
            c = per_row.get(row, Counter())
            total = sum(c.values())
            rows.append(
                {
                    "row": row,
                    "n": total,
                    "cells": [
                        {"column": col, "count": c.get(col, 0),
                         "pct": (100.0 * c.get(col, 0) / total) if total else 0.0}
                        for col in cols_cfg
                    ],
                }
            )
        return QuestionResult(kind="matrix", rows=rows, **base)

    if t == Question.Type.RANKING:
        items = [str(i) for i in (cfg.get("items") or [])]
        sums: dict[str, float] = {it: 0.0 for it in items}
        counts_n: dict[str, int] = {it: 0 for it in items}
        for v in values:
            if not isinstance(v, list):
                continue
            for rank, item in enumerate(v, start=1):
                key = str(item)
                if key not in sums:
                    sums[key] = 0.0
                    counts_n[key] = 0
                sums[key] += rank
                counts_n[key] += 1
        rows = [
            {
                "item": it,
                "mean_rank": (sums[it] / counts_n[it]) if counts_n[it] else None,
                "n": counts_n[it],
            }
            for it in sums
        ]
        rows.sort(key=lambda r: (r["mean_rank"] is None, r["mean_rank"] or 0))
        return QuestionResult(kind="ranking", rows=rows, **base)

    if t == Question.Type.TEXT:
        nonempty = [v for v in values if str(v).strip()]
        return QuestionResult(
            kind="text",
            n=len(nonempty),
            stats={"responses": len(nonempty)},
            question_id=question.pk,
            prompt=question.prompt,
            type=t,
            section=question.section,
        )

    # Plugin / unknown types: a response count (components may add richer
    # analysis later).
    return QuestionResult(kind="other", **base)


def experiment_question_analysis(experiment: Experiment) -> list[QuestionResult]:
    """Analyse every question in an experiment, in display order."""
    questions = experiment.questions.all().order_by("section", "sort_order", "id")
    return [analyse_question(experiment, q) for q in questions]
