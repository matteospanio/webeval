"""Shared querysets for *real* participant data.

"Real" data means sessions that were actually submitted and were not preview
(test-phase) sessions. That filter — ``submitted_at__isnull=False`` +
``is_preview=False`` — was copied across ~20 call sites in stats, analysis,
stats_tests, csv_exports, api and the studio; centralizing it here makes the
definition of "real data" a single source of truth.

Lives in the ``experiments`` analysis layer (which already imports
``survey.models`` downstream), not the ``experiments.models`` core, so the
``survey → experiments`` dependency direction is preserved.
"""
from __future__ import annotations

import json
from typing import Any, Iterable

from survey.models import PairAssignment, ParticipantSession, Response


def real_sessions(experiment, **extra):
    """Submitted, non-preview :class:`~survey.models.ParticipantSession` rows
    for ``experiment`` (extra keyword filters are ANDed in)."""
    return ParticipantSession.objects.filter(
        experiment=experiment,
        submitted_at__isnull=False,
        is_preview=False,
        **extra,
    )


def real_responses(experiment, **extra):
    """:class:`~survey.models.Response` rows belonging to submitted, non-preview
    sessions of ``experiment`` (extra keyword filters are ANDed in — e.g.
    ``question=q`` or ``stimulus__isnull=False``)."""
    return Response.objects.filter(
        session__experiment=experiment,
        session__submitted_at__isnull=False,
        session__is_preview=False,
        **extra,
    )


def real_pair_assignments(experiment, **extra):
    """:class:`~survey.models.PairAssignment` rows from submitted, non-preview
    sessions of ``experiment`` (pairwise stats)."""
    return PairAssignment.objects.filter(
        session__experiment=experiment,
        session__submitted_at__isnull=False,
        session__is_preview=False,
        **extra,
    )


def decoded_answers(raws: Iterable[str]) -> list[Any]:
    """JSON-decode a sequence of stored ``answer_value`` strings, skipping any
    that don't parse (the try/except duplicated across every analysis site)."""
    out: list[Any] = []
    for raw in raws:
        try:
            out.append(json.loads(raw))
        except (TypeError, ValueError):
            continue
    return out
