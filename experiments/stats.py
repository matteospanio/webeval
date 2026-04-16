"""Aggregate statistics for experiments.

Helpers here return plain Python dicts/lists instead of QuerySets so they
can be passed straight to admin templates, JSON endpoints, or the chart
layer without further massaging. Each function is intentionally small so
that unit tests can hand-compute expected values from a fixture.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from statistics import mean
from typing import Any

from django.db.models import Count

from experiments.models import Experiment, Question, Stimulus
from survey.models import PairAssignment, ParticipantSession, Response


@dataclass
class ExperimentCounts:
    consent_page_views: int
    total_sessions: int
    completed_sessions: int
    abandoned_sessions: int

    @property
    def completion_rate(self) -> float:
        if self.total_sessions == 0:
            return 0.0
        return self.completed_sessions / self.total_sessions

    @property
    def dropout_rate(self) -> float:
        if self.total_sessions == 0:
            return 0.0
        return self.abandoned_sessions / self.total_sessions


@dataclass
class GlobalSummary:
    total_experiments: int
    drafts: int
    active: int
    closed: int
    total_sessions: int
    completed_sessions: int
    total_responses: int

    @property
    def completion_rate(self) -> float:
        if self.total_sessions == 0:
            return 0.0
        return self.completed_sessions / self.total_sessions


def global_summary() -> GlobalSummary:
    """Aggregate counts for the /admin/ summary cards."""
    by_state: dict[str, int] = {
        row["state"]: row["n"]
        for row in Experiment.objects.values("state").annotate(n=Count("pk"))
    }
    total_experiments = sum(by_state.values())
    total_sessions = ParticipantSession.objects.count()
    completed_sessions = ParticipantSession.objects.filter(
        submitted_at__isnull=False
    ).count()
    total_responses = Response.objects.filter(
        session__submitted_at__isnull=False
    ).count()
    return GlobalSummary(
        total_experiments=total_experiments,
        drafts=by_state.get(Experiment.State.DRAFT, 0),
        active=by_state.get(Experiment.State.ACTIVE, 0),
        closed=by_state.get(Experiment.State.CLOSED, 0),
        total_sessions=total_sessions,
        completed_sessions=completed_sessions,
        total_responses=total_responses,
    )


def experiment_counts(experiment: Experiment) -> ExperimentCounts:
    """Return consent views, total, completed, abandoned session counts."""
    sessions = ParticipantSession.objects.filter(experiment=experiment)
    total = sessions.count()
    completed = sessions.filter(submitted_at__isnull=False).count()
    return ExperimentCounts(
        consent_page_views=experiment.consent_page_views,
        total_sessions=total,
        completed_sessions=completed,
        abandoned_sessions=total - completed,
    )


def mean_listen_duration_ms(experiment: Experiment) -> float | None:
    """Mean ``listen_duration_ms`` across completed sessions, or None if empty."""
    qs = (
        ParticipantSession.objects.filter(
            experiment=experiment, submitted_at__isnull=False
        )
        .values("assignments__listen_duration_ms")
    )
    values = [
        row["assignments__listen_duration_ms"]
        for row in qs
        if row["assignments__listen_duration_ms"] is not None
    ]
    if not values:
        return None
    return float(mean(values))


@dataclass
class PairwiseCounts:
    total_pairs_shown: int
    per_model_appearances: dict[str, int]
    per_model_wins: dict[str, dict[str, int]]  # model -> {question_prompt: wins}


def pairwise_experiment_stats(experiment: Experiment) -> PairwiseCounts:
    """Aggregate pairwise stats: how many pairs shown, per-model win rates."""
    pairs = PairAssignment.objects.filter(
        session__experiment=experiment,
        session__submitted_at__isnull=False,
    ).select_related(
        "stimulus_a__condition", "stimulus_b__condition"
    )

    total = 0
    appearances: dict[str, int] = {}
    wins: dict[str, dict[str, int]] = {}

    for pa in pairs:
        total += 1
        model_a = pa.stimulus_a.condition.name
        model_b = pa.stimulus_b.condition.name
        appearances[model_a] = appearances.get(model_a, 0) + 1
        appearances[model_b] = appearances.get(model_b, 0) + 1

        for resp in pa.responses.select_related("question"):
            prompt = resp.question.prompt[:80]
            answer = resp.get_answer()
            # answer is "A" or "B" for pairwise choice questions
            if answer == "A":
                # A is the left stimulus
                winner = model_a if pa.position_a == "left" else model_b
            elif answer == "B":
                winner = model_b if pa.position_a == "left" else model_a
            else:
                continue
            wins.setdefault(winner, {})
            wins[winner][prompt] = wins[winner].get(prompt, 0) + 1

    return PairwiseCounts(
        total_pairs_shown=total,
        per_model_appearances=appearances,
        per_model_wins=wins,
    )


def per_stimulus_mean_ratings(experiment: Experiment) -> list[dict[str, Any]]:
    """Mean rating per stimulus, restricted to completed sessions.

    Returned rows look like::

        {"stimulus_id": 7, "title": "stim-a", "condition": "A", "mean": 70.0, "n": 3}

    The caller is responsible for sorting; rows come out in Stimulus default
    order (``condition``, ``sort_order``, ``title``).
    """
    rating_questions = experiment.questions.filter(
        type=Question.Type.RATING, section=Question.Section.STIMULUS
    )
    if not rating_questions.exists():
        return []

    stimuli = Stimulus.objects.filter(
        condition__experiment=experiment
    ).select_related("condition")

    rows: list[dict[str, Any]] = []
    for stim in stimuli:
        responses = Response.objects.filter(
            session__experiment=experiment,
            session__submitted_at__isnull=False,
            stimulus=stim,
            question__in=rating_questions,
        ).values_list("answer_value", flat=True)
        numeric: list[float] = []
        for raw in responses:
            try:
                value = json.loads(raw)
            except (TypeError, ValueError):
                continue
            try:
                numeric.append(float(value))
            except (TypeError, ValueError):
                continue
        if not numeric:
            continue
        rows.append(
            {
                "stimulus_id": stim.pk,
                "title": stim.title,
                "condition": stim.condition.name,
                "mean": mean(numeric),
                "n": len(numeric),
            }
        )
    return rows
