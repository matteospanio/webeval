"""Aggregate statistics for experiments.

Helpers here return plain Python dicts/lists instead of QuerySets so they
can be passed straight to admin templates, JSON endpoints, or the chart
layer without further massaging. Each function is intentionally small so
that unit tests can hand-compute expected values from a fixture.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from statistics import mean
from typing import Any

import numpy as np
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


@dataclass
class BradleyTerryResult:
    """Bradley-Terry scores for one evaluation dimension."""

    dimension: str
    models: list[str]
    scores: list[float]       # log-strength (reference model = 0)
    se: list[float]           # standard errors
    wins: list[int]
    totals: list[int]

    @property
    def rows(self) -> list[dict[str, Any]]:
        """Return a list of dicts sorted by score descending, ready for templates."""
        items = []
        for i, m in enumerate(self.models):
            items.append(
                {
                    "model": m,
                    "score": self.scores[i],
                    "ci_lo": self.scores[i] - 1.96 * self.se[i],
                    "ci_hi": self.scores[i] + 1.96 * self.se[i],
                    "wins": self.wins[i],
                    "total": self.totals[i],
                    "win_pct": 100.0 * self.wins[i] / self.totals[i]
                    if self.totals[i]
                    else 0.0,
                }
            )
        items.sort(key=lambda r: r["score"], reverse=True)
        for rank, item in enumerate(items, 1):
            item["rank"] = rank
        return items


@dataclass
class BradleyTerryStats:
    """Per-dimension Bradley-Terry results for a pairwise experiment."""

    dimensions: list[BradleyTerryResult] = field(default_factory=list)

    @property
    def summary_rows(self) -> list[dict[str, Any]]:
        """Combined table: one row per model, one column per dimension.

        Sorted by mean rank across dimensions.
        """
        if not self.dimensions:
            return []
        all_models = self.dimensions[0].models
        model_data: dict[str, dict[str, Any]] = {m: {"model": m} for m in all_models}
        model_ranks: dict[str, list[int]] = {m: [] for m in all_models}

        for dim in self.dimensions:
            for row in dim.rows:
                model_data[row["model"]][dim.dimension] = row
                model_ranks[row["model"]].append(row["rank"])

        result = list(model_data.values())
        result.sort(key=lambda r: np.mean(model_ranks[r["model"]]))
        return result


def _fit_bradley_terry_mm(
    win_matrix: np.ndarray, max_iter: int = 1000, tol: float = 1e-8
) -> np.ndarray:
    """Fit BT strengths via the MM (minorisation-maximisation) algorithm.

    Parameters
    ----------
    win_matrix : ndarray of shape (K, K)
        ``win_matrix[i, j]`` = number of times model *i* beat model *j*.

    Returns
    -------
    beta : ndarray of shape (K,)
        Log-strength parameters (normalised so ``beta[0] = 0``).
    """
    K = win_matrix.shape[0]
    p = np.ones(K)  # start with uniform strengths

    for _ in range(max_iter):
        p_old = p.copy()
        for i in range(K):
            w_i = win_matrix[i].sum()
            if w_i == 0:
                continue
            denom = 0.0
            for j in range(K):
                n_ij = win_matrix[i, j] + win_matrix[j, i]
                if n_ij == 0:
                    continue
                denom += n_ij / (p[i] + p[j])
            if denom > 0:
                p[i] = w_i / denom
        # Normalise so the geometric mean is 1.
        p /= np.exp(np.mean(np.log(np.maximum(p, 1e-300))))
        if np.max(np.abs(p - p_old)) < tol:
            break

    beta = np.log(np.maximum(p, 1e-300))
    beta -= beta[0]  # reference model = 0
    return beta


def _bt_standard_errors(beta: np.ndarray, win_matrix: np.ndarray) -> np.ndarray:
    """Compute standard errors from the Fisher information Hessian."""
    K = len(beta)
    if K < 2:
        return np.zeros(K)

    # Build Fisher information for free parameters (indices 1..K-1).
    H = np.zeros((K - 1, K - 1))
    for i in range(K):
        for j in range(i + 1, K):
            n_ij = win_matrix[i, j] + win_matrix[j, i]
            if n_ij == 0:
                continue
            p_ij = 1.0 / (1.0 + np.exp(beta[j] - beta[i]))
            fisher = n_ij * p_ij * (1.0 - p_ij)
            fi, fj = i - 1, j - 1
            if fi >= 0:
                H[fi, fi] += fisher
            if fj >= 0:
                H[fj, fj] += fisher
            if fi >= 0 and fj >= 0:
                H[fi, fj] -= fisher
                H[fj, fi] -= fisher

    H += 1e-8 * np.eye(K - 1)  # ridge for sparse pairs
    try:
        cov = np.linalg.inv(H)
        se_free = np.sqrt(np.maximum(np.diag(cov), 0.0))
    except np.linalg.LinAlgError:
        se_free = np.full(K - 1, np.nan)

    se = np.zeros(K)
    se[1:] = se_free
    return se


def bradley_terry_analysis(experiment: Experiment) -> BradleyTerryStats:
    """Fit a Bradley-Terry model per dimension for a pairwise experiment.

    Returns a :class:`BradleyTerryStats` with one :class:`BradleyTerryResult`
    per evaluation question.
    """
    pairs = (
        PairAssignment.objects.filter(
            session__experiment=experiment,
            session__submitted_at__isnull=False,
        )
        .select_related("stimulus_a__condition", "stimulus_b__condition")
        .prefetch_related("responses__question")
    )

    # Collect all unique models and questions.
    models_set: set[str] = set()
    # question_id -> prompt label
    question_labels: dict[int, str] = {}
    # question_id -> list of (winner, loser) tuples
    comparisons: dict[int, list[tuple[str, str]]] = {}

    for pa in pairs:
        model_a = pa.stimulus_a.condition.name
        model_b = pa.stimulus_b.condition.name
        models_set.add(model_a)
        models_set.add(model_b)

        for resp in pa.responses.all():
            qid = resp.question_id
            if qid not in question_labels:
                question_labels[qid] = resp.question.prompt[:80]
            answer = resp.get_answer()
            if answer == "A":
                winner = model_a if pa.position_a == "left" else model_b
                loser = model_b if pa.position_a == "left" else model_a
            elif answer == "B":
                winner = model_b if pa.position_a == "left" else model_a
                loser = model_a if pa.position_a == "left" else model_b
            else:
                continue
            comparisons.setdefault(qid, []).append((winner, loser))

    if not models_set:
        return BradleyTerryStats()

    models = sorted(models_set)
    idx = {m: i for i, m in enumerate(models)}
    K = len(models)

    results: list[BradleyTerryResult] = []
    for qid in sorted(question_labels, key=lambda q: question_labels[q]):
        label = question_labels[qid]
        comps = comparisons.get(qid, [])
        if not comps:
            continue

        W = np.zeros((K, K))
        for winner, loser in comps:
            W[idx[winner], idx[loser]] += 1

        beta = _fit_bradley_terry_mm(W)
        se = _bt_standard_errors(beta, W)

        wins = [int(W[i].sum()) for i in range(K)]
        totals = [int(W[i].sum() + W[:, i].sum()) for i in range(K)]

        results.append(
            BradleyTerryResult(
                dimension=label,
                models=models,
                scores=[float(beta[i]) for i in range(K)],
                se=[float(se[i]) for i in range(K)],
                wins=wins,
                totals=totals,
            )
        )

    return BradleyTerryStats(dimensions=results)


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
