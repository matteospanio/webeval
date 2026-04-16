"""Survey app models: the anonymous, per-submission participant state.

Each time a participant opens the consent page of an active experiment we
create a new :class:`ParticipantSession` (identified by a UUID stored in the
Django session). The session carries:

* its lifecycle timestamps (consent, submit, abandon) for drop-off analysis;
* lightweight metadata captured once at entry (device type, browser family,
  country code); and
* a ``last_step`` cursor so the flow state-machine in :mod:`survey.flow`
  can answer "what should the participant see next?".

:class:`StimulusAssignment` is the per-session ordering produced by the
assignment strategy; it also records how long the participant actually
listened to each stimulus (updated by a small async POST from the client).

:class:`Response` is one row per answered question, serialised as JSON text
so the single column works uniformly for rating / choice / text answers.
"""
from __future__ import annotations

import json
import uuid
from typing import Any

from django.db import models


class ParticipantSession(models.Model):
    class Step(models.TextChoices):
        CONSENT = "consent", "Consent"
        INSTRUCTIONS = "instructions", "Instructions"
        AUDIO_CHECK = "audio_check", "Audio playback check"
        STIMULI = "stimuli", "Listening to stimuli"
        DEMOGRAPHICS = "demographics", "Demographic questions"
        DONE = "done", "Completed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    experiment = models.ForeignKey(
        "experiments.Experiment",
        on_delete=models.CASCADE,
        related_name="sessions",
    )

    last_step = models.CharField(
        max_length=16,
        choices=Step.choices,
        default=Step.CONSENT,
    )

    # PsyToolkit-style per-page cursors. The stimulus phase walks
    # ``current_assignment_index`` (over the ordered list of StimulusAssignment
    # rows for this session) and ``current_page_index`` (0-based into the list
    # of pages computed from Question.page_break_before for stimulus
    # questions). The demographic phase uses ``demographic_page_index``.
    # Pairwise mode uses ``current_pair_index`` instead of the assignment
    # cursors (one page per pair, all questions on one page).
    current_assignment_index = models.PositiveSmallIntegerField(default=0)
    current_page_index = models.PositiveSmallIntegerField(default=0)
    current_pair_index = models.PositiveSmallIntegerField(default=0)
    demographic_page_index = models.PositiveSmallIntegerField(default=0)

    started_at = models.DateTimeField(auto_now_add=True)
    consented_at = models.DateTimeField(null=True, blank=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    abandoned_at = models.DateTimeField(null=True, blank=True)

    device_type = models.CharField(max_length=16, blank=True)
    browser_family = models.CharField(max_length=64, blank=True)
    country_code = models.CharField(max_length=2, blank=True)

    class Meta:
        ordering = ("-started_at",)

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"session {self.id} ({self.last_step})"

    @property
    def is_complete(self) -> bool:
        return self.submitted_at is not None


class StimulusAssignment(models.Model):
    session = models.ForeignKey(
        ParticipantSession,
        on_delete=models.CASCADE,
        related_name="assignments",
    )
    stimulus = models.ForeignKey(
        "experiments.Stimulus",
        on_delete=models.CASCADE,
        related_name="assignments",
    )
    sort_order = models.PositiveIntegerField()
    started_listening_at = models.DateTimeField(null=True, blank=True)
    listen_duration_ms = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ("session", "sort_order")
        unique_together = (("session", "sort_order"),)


class PairAssignment(models.Model):
    """One pairwise comparison trial assigned to a participant session."""

    class Position(models.TextChoices):
        LEFT = "left", "Left"
        RIGHT = "right", "Right"

    session = models.ForeignKey(
        ParticipantSession,
        on_delete=models.CASCADE,
        related_name="pair_assignments",
    )
    stimulus_a = models.ForeignKey(
        "experiments.Stimulus",
        on_delete=models.CASCADE,
        related_name="pair_assignments_as_a",
    )
    stimulus_b = models.ForeignKey(
        "experiments.Stimulus",
        on_delete=models.CASCADE,
        related_name="pair_assignments_as_b",
    )
    prompt_group = models.CharField(max_length=200)
    position_a = models.CharField(
        max_length=5,
        choices=Position.choices,
        help_text="Which side stimulus A appears on (left or right).",
    )
    sort_order = models.PositiveIntegerField()
    listen_duration_a_ms = models.PositiveIntegerField(default=0)
    listen_duration_b_ms = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ("session", "sort_order")
        unique_together = (("session", "sort_order"),)

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"pair {self.sort_order} ({self.stimulus_a} vs {self.stimulus_b})"

    @property
    def left_stimulus(self):
        return self.stimulus_a if self.position_a == self.Position.LEFT else self.stimulus_b

    @property
    def right_stimulus(self):
        return self.stimulus_b if self.position_a == self.Position.LEFT else self.stimulus_a


class Response(models.Model):
    session = models.ForeignKey(
        ParticipantSession,
        on_delete=models.CASCADE,
        related_name="responses",
    )
    # Stimulus is nullable — demographic answers aren't tied to a stimulus.
    stimulus = models.ForeignKey(
        "experiments.Stimulus",
        on_delete=models.CASCADE,
        related_name="responses",
        null=True,
        blank=True,
    )
    # For pairwise mode: links the response to the specific pair trial.
    pair_assignment = models.ForeignKey(
        PairAssignment,
        on_delete=models.CASCADE,
        related_name="responses",
        null=True,
        blank=True,
    )
    question = models.ForeignKey(
        "experiments.Question",
        on_delete=models.CASCADE,
        related_name="responses",
    )
    answer_value = models.TextField(
        help_text="JSON-encoded answer payload (int, str, or list depending on question type).",
    )
    answered_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("session", "answered_at")

    def set_answer(self, value: Any) -> None:
        self.answer_value = json.dumps(value, ensure_ascii=False)

    def get_answer(self) -> Any:
        if not self.answer_value:
            return None
        return json.loads(self.answer_value)
