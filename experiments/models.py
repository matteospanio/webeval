"""Domain models for the experiments app.

Four entities:

* ``Experiment`` — top-level study configuration, with a draft/active/closed
  lifecycle. Only ``draft`` experiments accept structural edits (adding or
  removing conditions, stimuli, questions); active experiments can still have
  their cosmetic fields (name, description, consent wording) tweaked.
* ``Condition`` — a category that a stimulus belongs to (e.g. a particular
  generation method). Used by the assignment strategy for balancing.
* ``Stimulus`` — one thing shown to the participant. A ``kind`` discriminator
  distinguishes audio clips (uploaded file, SHA-256 + duration autocomputed),
  images (uploaded file), and text-only prompts (``text_body``).
* ``Question`` — a prompt shown to participants. A single table handles the
  three supported types (``rating``, ``choice``, ``text``) via a ``config``
  JSONField; a ``section`` flag distinguishes per-stimulus questions from
  post-survey demographic questions. ``page_break_before`` lets an author
  split the item stream into pages PsyToolkit-style.
"""
from __future__ import annotations

import hashlib
import os
from typing import Any

from django.core.exceptions import ValidationError
from django.db import models
from django.utils.text import slugify

from .validators import (
    audio_extension_validator,
    audio_size_validator,
    image_extension_validator,
    image_size_validator,
)


# --- Experiment --------------------------------------------------------------


class Experiment(models.Model):
    class State(models.TextChoices):
        DRAFT = "draft", "Draft"
        ACTIVE = "active", "Active"
        CLOSED = "closed", "Closed"

    # Lifecycle transitions allowed when full_clean() is called on an update.
    _ALLOWED_TRANSITIONS: dict[str, set[str]] = {
        State.DRAFT: {State.DRAFT, State.ACTIVE, State.CLOSED},
        State.ACTIVE: {State.DRAFT, State.ACTIVE, State.CLOSED},
        State.CLOSED: {State.DRAFT, State.ACTIVE, State.CLOSED},
    }

    class Mode(models.TextChoices):
        STANDARD = "standard", "Standard (single stimulus)"
        PAIRWISE = "pairwise", "Pairwise comparison"

    name = models.CharField(max_length=200)
    slug = models.SlugField(unique=True, max_length=200)
    description = models.TextField(blank=True)
    consent_text = models.TextField(
        blank=True,
        help_text="Markdown, shown to participants on the consent page.",
    )
    instructions_content = models.TextField(
        blank=True,
        help_text="Markdown for the instructions page. Leave blank for default text.",
    )
    thanks_content = models.TextField(
        blank=True,
        help_text="Markdown for the thanks/completion page. Leave blank for default text.",
    )
    privacy_contact = models.CharField(max_length=200, blank=True)
    privacy_policy_url = models.URLField(blank=True)

    state = models.CharField(
        max_length=16,
        choices=State.choices,
        default=State.DRAFT,
    )
    mode = models.CharField(
        max_length=16,
        choices=Mode.choices,
        default=Mode.STANDARD,
        help_text="Standard shows one stimulus at a time; pairwise shows two for comparison.",
    )

    stimuli_per_participant = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text=(
            "Standard mode: how many stimuli each participant sees. Blank = all. "
            "Pairwise mode: how many comparison pairs per participant."
        ),
    )
    assignment_strategy = models.CharField(
        max_length=64,
        default="balanced_random",
        help_text="Identifier of a strategy registered in experiments.assignment.",
    )
    require_audio_check = models.BooleanField(
        default=True,
        help_text=(
            "If enabled, and the experiment contains audio stimuli, participants "
            "play a short test tone and confirm the volume is comfortable before "
            "the first stimulus. Ignored when no audio stimuli are configured."
        ),
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)[:200]
        super().save(*args, **kwargs)

    def clean(self):
        super().clean()
        if self.pk:
            old = (
                Experiment.objects.filter(pk=self.pk)
                .values_list("state", "mode")
                .first()
            )
            if old is not None:
                old_state, old_mode = old
                allowed = self._ALLOWED_TRANSITIONS.get(old_state, set())
                if self.state not in allowed:
                    raise ValidationError(
                        {
                            "state": (
                                f"Cannot transition from {old_state} to {self.state}. "
                                f"Allowed targets: {sorted(allowed)}."
                            )
                        }
                    )
                # Mode cannot change once the experiment has left draft.
                if old_state != self.State.DRAFT and self.mode != old_mode:
                    raise ValidationError(
                        {"mode": "Mode cannot be changed after the experiment leaves draft."}
                    )


# --- Structural-edit guard for child models ---------------------------------


def _ensure_draft(experiment: "Experiment | None") -> None:
    """Raise ValidationError if the parent experiment is not in draft state.

    The check reads the parent's *committed* state from the database rather
    than the in-memory instance. This matters when the admin flips an
    experiment from draft to active: Django's inline formsets re-validate
    every child row against the parent instance (which now holds
    ``state=active`` in memory), so a naive in-memory check would wrongly
    block the transition. Reading the DB row instead means "child edits are
    gated by whatever state is currently persisted," which is what users
    actually want.

    Unsaved parents (``pk is None``) are treated as draft so a factory
    like ``ConditionFactory.build(experiment=exp)`` still passes when the
    parent hasn't been persisted yet.
    """
    if experiment is None:
        return
    if experiment.pk is None:
        committed_state = experiment.state
    else:
        committed_state = (
            Experiment.objects.filter(pk=experiment.pk)
            .values_list("state", flat=True)
            .first()
        )
        if committed_state is None:
            # Parent was deleted mid-flight; let the DB layer handle it.
            return
    if committed_state != Experiment.State.DRAFT:
        display = dict(Experiment.State.choices).get(committed_state, committed_state)
        raise ValidationError(
            f"Experiment '{experiment.name}' is {display.lower()}; "
            "conditions, stimuli, and questions can only be added, edited, or "
            "removed while the experiment is in draft state."
        )


# --- Condition ---------------------------------------------------------------


class Condition(models.Model):
    experiment = models.ForeignKey(
        Experiment,
        on_delete=models.CASCADE,
        related_name="conditions",
    )
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)

    class Meta:
        unique_together = ("experiment", "name")
        ordering = ("experiment", "name")

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"{self.experiment.name} / {self.name}"

    def clean(self):
        super().clean()
        _ensure_draft(self.experiment)

    def delete(self, *args, **kwargs):
        _ensure_draft(self.experiment)
        return super().delete(*args, **kwargs)


# --- Stimulus ----------------------------------------------------------------


def _stimulus_upload_path(instance: "Stimulus", filename: str) -> str:
    experiment_id = instance.condition.experiment_id if instance.condition_id else "unassigned"
    return f"stimuli/{experiment_id}/{filename}"


class Stimulus(models.Model):
    class Kind(models.TextChoices):
        AUDIO = "audio", "Audio clip"
        IMAGE = "image", "Image"
        TEXT = "text", "Text only"

    condition = models.ForeignKey(
        Condition,
        on_delete=models.CASCADE,
        related_name="stimuli",
    )
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)

    kind = models.CharField(
        max_length=8,
        choices=Kind.choices,
        default=Kind.AUDIO,
        help_text="What the participant sees/hears on the stimulus page.",
    )

    # Present only when kind == AUDIO.
    audio = models.FileField(
        upload_to=_stimulus_upload_path,
        null=True,
        blank=True,
        validators=[audio_extension_validator(), audio_size_validator],
    )
    # Present only when kind == IMAGE.
    image = models.FileField(
        upload_to=_stimulus_upload_path,
        null=True,
        blank=True,
        validators=[image_extension_validator(), image_size_validator],
    )
    # Present only when kind == TEXT (rendered with |linebreaks).
    text_body = models.TextField(
        blank=True,
        help_text="Used when kind = Text only. Rendered with line breaks preserved.",
    )

    duration_seconds = models.FloatField(null=True, blank=True)
    sha256 = models.CharField(max_length=64, blank=True)

    prompt_group = models.CharField(
        max_length=200,
        blank=True,
        db_index=True,
        help_text=(
            "Groups stimuli across conditions by shared prompt. "
            "Required for pairwise experiments."
        ),
    )

    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ("condition", "sort_order", "title")

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.title

    @property
    def experiment(self) -> Experiment:
        return self.condition.experiment

    def clean(self):
        super().clean()
        _ensure_draft(self.condition.experiment if self.condition_id else None)
        self._validate_kind_fields()

    def _validate_kind_fields(self) -> None:
        errors: dict[str, str] = {}
        if self.kind == self.Kind.AUDIO:
            if not self.audio:
                errors["audio"] = "Audio stimuli require an uploaded audio file."
            if self.image:
                errors["image"] = "Audio stimuli must not carry an image."
        elif self.kind == self.Kind.IMAGE:
            if not self.image:
                errors["image"] = "Image stimuli require an uploaded image file."
            if self.audio:
                errors["audio"] = "Image stimuli must not carry an audio file."
        elif self.kind == self.Kind.TEXT:
            if not (self.text_body or "").strip():
                errors["text_body"] = "Text stimuli require non-empty text."
            if self.audio:
                errors["audio"] = "Text stimuli must not carry an audio file."
            if self.image:
                errors["image"] = "Text stimuli must not carry an image."
        if errors:
            raise ValidationError(errors)

    def delete(self, *args, **kwargs):
        _ensure_draft(self.condition.experiment if self.condition_id else None)
        return super().delete(*args, **kwargs)

    def save(self, *args, **kwargs):
        # SHA-256 of the source media, whichever exists for this kind. We
        # only compute it once per stimulus; the field stays empty on
        # text-only stimuli (nothing to hash).
        if not self.sha256:
            source = self._media_field()
            if source is not None:
                try:
                    source.open("rb")
                    source.seek(0)
                    hasher = hashlib.sha256()
                    for chunk in iter(lambda: source.read(65536), b""):
                        hasher.update(chunk)
                    self.sha256 = hasher.hexdigest()
                except Exception:
                    # Never block a save on checksum failure.
                    pass
                finally:
                    try:
                        source.seek(0)
                    except Exception:
                        pass

        super().save(*args, **kwargs)

        # Duration only makes sense for audio; mutagen reads metadata from
        # the stored file path.
        if (
            self.kind == self.Kind.AUDIO
            and self.duration_seconds is None
            and self.audio
        ):
            duration = _safe_duration_seconds(
                self.audio.path if _has_path(self.audio) else None
            )
            if duration is not None:
                type(self).objects.filter(pk=self.pk).update(duration_seconds=duration)
                self.duration_seconds = duration

    def _media_field(self):
        """Return the FileField currently holding this stimulus' media, or None."""
        if self.kind == self.Kind.AUDIO and self.audio:
            return self.audio
        if self.kind == self.Kind.IMAGE and self.image:
            return self.image
        return None


def _has_path(file_field) -> bool:
    try:
        _ = file_field.path
        return True
    except (NotImplementedError, ValueError):
        return False


def _safe_duration_seconds(path: str | None) -> float | None:
    if not path or not os.path.exists(path):
        return None
    try:
        from mutagen import File as MutagenFile

        m = MutagenFile(path)
        if m is not None and getattr(m, "info", None) is not None:
            length = getattr(m.info, "length", None)
            if length is not None:
                return float(length)
    except Exception:
        return None
    return None


# --- Question ----------------------------------------------------------------


class Question(models.Model):
    class Section(models.TextChoices):
        STIMULUS = "stimulus", "Asked per stimulus"
        DEMOGRAPHIC = "demographic", "Post-survey demographics"

    class Type(models.TextChoices):
        RATING = "rating", "Rating slider"
        CHOICE = "choice", "Multiple choice"
        TEXT = "text", "Free text"
        LIKERT = "likert", "Likert scale"

    experiment = models.ForeignKey(
        Experiment,
        on_delete=models.CASCADE,
        related_name="questions",
    )
    section = models.CharField(max_length=16, choices=Section.choices)
    type = models.CharField(max_length=16, choices=Type.choices)

    prompt = models.TextField(help_text="Supports Markdown.")
    help_text = models.TextField(blank=True)
    required = models.BooleanField(default=True)

    config = models.JSONField(
        default=dict,
        blank=True,
        help_text=(
            "Per-type configuration. "
            "rating: {min, max, step, min_label?, max_label?}. "
            "choice: {choices: [...], multi: bool}. "
            "text: {max_length}. "
            "likert: {steps: int, labels: [str, ...]}."
        ),
    )

    sort_order = models.PositiveIntegerField(default=0)
    page_break_before = models.BooleanField(
        default=False,
        help_text=(
            "Start a new page before this question (PsyToolkit-style page "
            "break). The first question of a section always starts a new "
            "page implicitly; check this to split subsequent questions "
            "onto their own pages."
        ),
    )

    class Meta:
        ordering = ("experiment", "section", "sort_order", "id")

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"[{self.get_section_display()}] {self.prompt[:60]}"

    def clean(self):
        super().clean()
        _ensure_draft(self.experiment if self.experiment_id else None)
        _validate_question_config(self.type, self.config or {})

    def delete(self, *args, **kwargs):
        _ensure_draft(self.experiment if self.experiment_id else None)
        return super().delete(*args, **kwargs)


def _validate_question_config(question_type: str, config: dict[str, Any]) -> None:
    if not isinstance(config, dict):
        raise ValidationError(
            {
                "config": (
                    "config must be a JSON object (for example "
                    '{"min": 0, "max": 100, "step": 1}).'
                )
            }
        )

    if question_type == Question.Type.RATING:
        required_keys = {"min", "max", "step"}
        missing = required_keys - config.keys()
        if missing:
            raise ValidationError(
                {"config": f"rating questions require keys {sorted(required_keys)}; missing {sorted(missing)}."}
            )
        try:
            low = int(config["min"])
            high = int(config["max"])
            step = int(config["step"])
        except (TypeError, ValueError) as exc:
            raise ValidationError({"config": "rating min/max/step must be integers."}) from exc
        if step <= 0:
            raise ValidationError({"config": "rating step must be positive."})
        if low >= high:
            raise ValidationError({"config": "rating min must be strictly less than max."})
        for label_key in ("min_label", "max_label"):
            if label_key in config and not isinstance(config[label_key], str):
                raise ValidationError(
                    {"config": f"rating {label_key!r} must be a string if present."}
                )
        return

    if question_type == Question.Type.CHOICE:
        choices = config.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ValidationError(
                {"config": "choice questions require a non-empty 'choices' list."}
            )
        if not all(isinstance(c, str) and c for c in choices):
            raise ValidationError({"config": "every choice must be a non-empty string."})
        return

    if question_type == Question.Type.TEXT:
        max_length = config.get("max_length")
        if not isinstance(max_length, int) or max_length <= 0:
            raise ValidationError(
                {"config": "text questions require a positive integer 'max_length'."}
            )
        return

    if question_type == Question.Type.LIKERT:
        steps = config.get("steps")
        if not isinstance(steps, int) or not (2 <= steps <= 11):
            raise ValidationError(
                {"config": "likert questions require an integer 'steps' between 2 and 11."}
            )
        labels = config.get("labels")
        if not isinstance(labels, list) or len(labels) != steps:
            raise ValidationError(
                {"config": f"likert questions require a 'labels' list of exactly {steps} strings."}
            )
        if not all(isinstance(lb, str) and lb for lb in labels):
            raise ValidationError(
                {"config": "every likert label must be a non-empty string."}
            )
        return

    raise ValidationError({"type": f"unknown question type: {question_type!r}"})
