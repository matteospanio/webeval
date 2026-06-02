"""Quality flags for completed participant sessions.

Computed once at completion (``survey.views._finish_session``) and stored on the
session so reports and exports can surface or exclude suspicious responses
without recomputing. Detects:

* failed attention checks — an answer that differs from a question's
  ``attention_expected`` value,
* speeders — finishing faster than ``Experiment.min_completion_seconds``,
* straight-lining — an identical answer to every rating/likert stimulus
  question.
"""
from __future__ import annotations

from .models import Response

FAILED_ATTENTION = "failed_attention"
SPEEDER = "speeder"
STRAIGHT_LINING = "straight_lining"

_SCALE_TYPES = {"rating", "likert"}
_STRAIGHT_LINING_MIN = 3


def compute_flags(session) -> tuple[int, list[str]]:
    """Return ``(failed_attention_count, flags)`` for a completed session."""
    flags: list[str] = []
    responses = list(
        Response.objects.filter(session=session).select_related("question")
    )

    failed = 0
    for r in responses:
        expected = r.question.attention_expected
        if expected is not None and r.get_answer() != expected:
            failed += 1
    if failed:
        flags.append(FAILED_ATTENTION)

    experiment = session.experiment
    if (
        experiment.min_completion_seconds
        and session.consented_at
        and session.submitted_at
    ):
        elapsed = (session.submitted_at - session.consented_at).total_seconds()
        if elapsed < experiment.min_completion_seconds:
            flags.append(SPEEDER)

    scale_answers = [
        r.get_answer()
        for r in responses
        if r.stimulus_id is not None and r.question.type in _SCALE_TYPES
    ]
    if (
        len(scale_answers) >= _STRAIGHT_LINING_MIN
        and len({str(a) for a in scale_answers}) == 1
    ):
        flags.append(STRAIGHT_LINING)

    return failed, flags
