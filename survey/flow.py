"""Participant-flow helpers.

The participant session advances through a fixed sequence of steps:

    consent → (audio_check) → instructions → stimuli → demographics → done

Within the ``stimuli`` and ``demographics`` steps the participant walks
through a sequence of *pages* (PsyToolkit-style): each page shows one or
more questions and posts its answers when the participant clicks Next. A
new page is started either implicitly (first question of a section /
stimulus) or explicitly when an author checks ``page_break_before`` on a
Question.

This module owns three things:

1. The mapping from ``ParticipantSession.Step`` to a view URL.
2. :func:`paginate_questions` — the page-break → list[list[Question]]
   helper, reused by the play and demographics views and by ``progress``
   calculations.
3. :func:`progress_percent` — a single-source-of-truth for the thin
   PsyToolkit progress bar shown at the top of every participant page.
"""
from __future__ import annotations

from typing import Iterable, TYPE_CHECKING

from django.urls import reverse

from .models import ParticipantSession

if TYPE_CHECKING:  # pragma: no cover - typing only
    from experiments.models import Question


STEP_URL_NAMES = {
    ParticipantSession.Step.CONSENT: "survey:consent",
    ParticipantSession.Step.INSTRUCTIONS: "survey:instructions",
    ParticipantSession.Step.AUDIO_CHECK: "survey:audio_check",
    ParticipantSession.Step.STIMULI: "survey:play",
    ParticipantSession.Step.DEMOGRAPHICS: "survey:demographics",
    ParticipantSession.Step.DONE: "survey:thanks",
}


def required_step_url(session: ParticipantSession) -> str:
    step = session.last_step
    if (
        step == ParticipantSession.Step.STIMULI
        and session.experiment.is_pairwise
    ):
        name = "survey:pairwise_play"
    else:
        name = STEP_URL_NAMES[step]
    return reverse(name, kwargs={"slug": session.experiment.slug})


def advance(session: ParticipantSession, to: str) -> None:
    """Persist a step transition. Callers are responsible for ordering sanity;
    this helper exists so the field name lives in exactly one place."""
    session.last_step = to
    session.save(update_fields=["last_step"])


def paginate_questions(questions: Iterable["Question"]) -> list[list["Question"]]:
    """Group an ordered question sequence into PsyToolkit-style pages.

    The first question of the sequence always starts a new page. Any
    subsequent question with ``page_break_before=True`` also starts a new
    page; otherwise it joins the previous page.

    An empty input yields an empty list (no pages).
    """
    pages: list[list["Question"]] = []
    for q in questions:
        if not pages or q.page_break_before:
            pages.append([q])
        else:
            pages[-1].append(q)
    return pages


def progress_percent(
    session: ParticipantSession,
    *,
    stimulus_pages_per_assignment: int,
    demographic_pages: int,
    assignments_total: int,
    audio_check: bool = False,
) -> int:
    """Return 0..100 reflecting the participant's position across the full survey.

    Pages counted (in order): consent (1), audio_check (0 or 1),
    instructions (1), ``assignments_total * stimulus_pages_per_assignment``
    stimulus pages, ``demographic_pages`` demographic pages, thanks (1).

    The *currently rendering* page is counted as "in progress" but not yet
    done: ``progress_percent`` is the fraction of pages already completed
    (i.e. whose POST has been accepted).
    """
    audio_pages = 1 if audio_check else 0
    pre_stim = 2 + audio_pages
    total_pages = (
        pre_stim
        + assignments_total * stimulus_pages_per_assignment
        + demographic_pages
        + 1
    )
    if total_pages <= 0:
        return 0

    step = session.last_step
    if step == ParticipantSession.Step.CONSENT:
        done = 0
    elif step == ParticipantSession.Step.AUDIO_CHECK:
        done = 1
    elif step == ParticipantSession.Step.INSTRUCTIONS:
        done = 1 + audio_pages
    elif step == ParticipantSession.Step.STIMULI:
        done = (
            pre_stim
            + session.current_assignment_index * stimulus_pages_per_assignment
            + session.current_page_index
        )
    elif step == ParticipantSession.Step.DEMOGRAPHICS:
        done = (
            pre_stim
            + assignments_total * stimulus_pages_per_assignment
            + session.demographic_page_index
        )
    else:  # DONE
        done = total_pages

    pct = int(round(100 * done / total_pages))
    return max(0, min(100, pct))


def pairwise_progress_percent(
    session: ParticipantSession,
    *,
    pairs_total: int,
    demographic_pages: int,
    audio_check: bool = False,
) -> int:
    """Progress for pairwise mode: 1 page per pair, no multi-page per stimulus."""
    audio_pages = 1 if audio_check else 0
    pre_stim = 2 + audio_pages
    total_pages = pre_stim + pairs_total + demographic_pages + 1
    if total_pages <= 0:
        return 0

    step = session.last_step
    if step == ParticipantSession.Step.CONSENT:
        done = 0
    elif step == ParticipantSession.Step.AUDIO_CHECK:
        done = 1
    elif step == ParticipantSession.Step.INSTRUCTIONS:
        done = 1 + audio_pages
    elif step == ParticipantSession.Step.STIMULI:
        done = pre_stim + session.current_pair_index
    elif step == ParticipantSession.Step.DEMOGRAPHICS:
        done = pre_stim + pairs_total + session.demographic_page_index
    else:
        done = total_pages

    pct = int(round(100 * done / total_pages))
    return max(0, min(100, pct))
