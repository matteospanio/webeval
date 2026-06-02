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

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils.text import slugify

from .branching import OPERATORS, VALUELESS_OPS, iter_clauses
from .validators import (
    audio_extension_validator,
    audio_size_validator,
    image_extension_validator,
    image_size_validator,
    video_extension_validator,
    video_size_validator,
)


# --- Experiment --------------------------------------------------------------


class Experiment(models.Model):
    class State(models.TextChoices):
        DRAFT = "draft", "Draft"
        TEST = "test", "Test"
        ACTIVE = "active", "Active"
        CLOSED = "closed", "Closed"

    # Lifecycle transitions allowed when full_clean() is called on an update.
    _ALLOWED_TRANSITIONS: dict[str, set[str]] = {
        State.DRAFT: {State.DRAFT, State.TEST, State.ACTIVE, State.CLOSED},
        State.TEST: {State.DRAFT, State.TEST, State.ACTIVE, State.CLOSED},
        State.ACTIVE: {State.DRAFT, State.TEST, State.ACTIVE, State.CLOSED},
        State.CLOSED: {State.DRAFT, State.TEST, State.ACTIVE, State.CLOSED},
    }

    class Mode(models.TextChoices):
        STANDARD = "standard", "Standard (single stimulus)"
        PAIRWISE = "pairwise", "Pairwise comparison"
        PAIRWISE_AUDIO = "pairwise_audio", "Pairwise audio continuation"

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
    consent_page_views = models.PositiveIntegerField(
        default=0,
        editable=False,
        help_text="Number of times the consent page was loaded by a visitor.",
    )

    require_audio_check = models.BooleanField(
        default=True,
        help_text=(
            "If enabled, and the experiment contains audio stimuli, participants "
            "play a short test tone and confirm the volume is comfortable before "
            "the first stimulus. Ignored when no audio stimuli are configured."
        ),
    )

    randomize_stimulus_questions = models.BooleanField(
        default=True,
        help_text=(
            "If enabled (default), each participant sees the per-stimulus "
            "questions in a randomised order seeded by their session so the "
            "order is stable across page refreshes. Disable to show all "
            "participants the same order defined by Question.sort_order."
        ),
    )

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="owned_experiments",
        help_text=(
            "The researcher who owns this study. The owner and their "
            "collaborators are the only non-superusers who can see, edit, or "
            "export it. Managed from the studio dashboard."
        ),
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.name

    @property
    def is_pairwise(self) -> bool:
        """True for any pairwise-style mode (written or audio prompts)."""
        return self.mode in (self.Mode.PAIRWISE, self.Mode.PAIRWISE_AUDIO)

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
                # TEST → ACTIVE must go through the admin activate confirmation
                # page so the author explicitly chooses whether to wipe the
                # data collected during testing. The activate view bypasses
                # full_clean() by saving with update_fields=["state"].
                if (
                    old_state == self.State.TEST
                    and self.state == self.State.ACTIVE
                    and not getattr(self, "_activate_confirmed", False)
                ):
                    raise ValidationError(
                        {
                            "state": (
                                "Use the Activate button to promote a test "
                                "experiment to active — this lets you confirm "
                                "whether to reset data collected during testing."
                            )
                        }
                    )
                # Mode cannot change once the experiment has left draft.
                if old_state != self.State.DRAFT and self.mode != old_mode:
                    raise ValidationError(
                        {"mode": "Mode cannot be changed after the experiment leaves draft."}
                    )
                # Leaving draft into TEST/ACTIVE requires that a PAIRWISE_AUDIO
                # experiment has an audio Prompt row for every distinct
                # prompt_group referenced by its active stimuli, and that
                # every active stimulus is audio.
                if (
                    old_state == self.State.DRAFT
                    and self.state in (self.State.TEST, self.State.ACTIVE)
                    and self.mode == self.Mode.PAIRWISE_AUDIO
                ):
                    self._validate_pairwise_audio_activation()

    def _validate_pairwise_audio_activation(self) -> None:
        active_stimuli = Stimulus.objects.filter(
            condition__experiment=self, is_active=True
        )
        non_audio_exists = active_stimuli.exclude(kind=Stimulus.Kind.AUDIO).exists()
        if non_audio_exists:
            raise ValidationError(
                {
                    "mode": (
                        "Pairwise audio continuation experiments can only contain "
                        "audio stimuli; remove or deactivate non-audio stimuli first."
                    )
                }
            )
        active_groups = set(
            active_stimuli.exclude(prompt_group="")
            .values_list("prompt_group", flat=True)
            .distinct()
        )
        if not active_groups:
            raise ValidationError(
                {
                    "mode": (
                        "Pairwise audio continuation experiments require at least "
                        "one active stimulus with a non-empty prompt_group."
                    )
                }
            )
        existing_groups = set(
            Prompt.objects.filter(
                experiment=self, prompt_group__in=active_groups
            ).values_list("prompt_group", flat=True)
        )
        missing = sorted(active_groups - existing_groups)
        if missing:
            raise ValidationError(
                {
                    "mode": (
                        "Pairwise audio continuation experiments require an audio "
                        "Prompt for every prompt_group. Missing prompts for: "
                        f"{missing}."
                    )
                }
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
        VIDEO = "video", "Video"
        HTML = "html", "HTML snippet"
        EMBED = "embed", "Embedded URL (iframe)"

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
    # Present only when kind == VIDEO.
    video = models.FileField(
        upload_to=_stimulus_upload_path,
        null=True,
        blank=True,
        validators=[video_extension_validator(), video_size_validator],
    )
    # Present only when kind == TEXT (line breaks) or HTML (rendered as-is).
    text_body = models.TextField(
        blank=True,
        help_text=(
            "Used when kind = Text only (rendered with line breaks) or "
            "HTML snippet (rendered as raw HTML to participants)."
        ),
    )
    # Present only when kind == EMBED.
    embed_url = models.URLField(
        blank=True,
        help_text="External URL shown in an iframe when kind = Embedded URL.",
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
        """Enforce that each kind populates its own field and no foreign media.

        A single matrix keeps the six kinds consistent: every kind owns exactly
        one field, must populate it, and must not carry any of the others.
        """
        K = self.Kind
        present = {
            "audio": bool(self.audio),
            "image": bool(self.image),
            "video": bool(self.video),
            "text_body": bool((self.text_body or "").strip()),
            "embed_url": bool((self.embed_url or "").strip()),
        }
        required_field = {
            K.AUDIO: "audio",
            K.IMAGE: "image",
            K.VIDEO: "video",
            K.TEXT: "text_body",
            K.HTML: "text_body",
            K.EMBED: "embed_url",
        }[self.kind]
        labels = {
            "audio": "an audio file",
            "image": "an image",
            "video": "a video file",
            "text_body": "text content",
            "embed_url": "an embed URL",
        }
        kind_label = self.get_kind_display()
        errors: dict[str, str] = {}
        if not present[required_field]:
            errors[required_field] = (
                f"{kind_label} stimuli require {labels[required_field]}."
            )
        for field, is_present in present.items():
            if field != required_field and is_present:
                errors[field] = (
                    f"{kind_label} stimuli must not carry {labels[field]}."
                )
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

        # Duration makes sense for time-based media (audio + video); mutagen
        # reads metadata from the stored file path.
        media = self._media_field()
        if (
            self.kind in (self.Kind.AUDIO, self.Kind.VIDEO)
            and self.duration_seconds is None
            and media is not None
        ):
            duration = _safe_duration_seconds(
                media.path if _has_path(media) else None
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
        if self.kind == self.Kind.VIDEO and self.video:
            return self.video
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
        NUMERIC = "numeric", "Numeric input"
        MATRIX = "matrix", "Matrix (grid)"
        RANKING = "ranking", "Ranking / ordering"

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
            "likert: {steps: int, labels: [str, ...]}. "
            "numeric: {min?, max?, integer?, unit?}. "
            "matrix: {rows: [...], columns: [...]}. "
            "ranking: {items: [...]}."
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
    show_prompt = models.BooleanField(
        default=False,
        help_text=(
            "Display the stimulus generation prompt to the participant "
            "on this question's page."
        ),
    )
    visible_if = models.JSONField(
        default=dict,
        blank=True,
        help_text=(
            "Optional skip logic: show this question only when earlier answers "
            'match. JSON, e.g. {"question": 12, "op": "eq", "value": "Yes"} or '
            '{"all": [clauses]} / {"any": [clauses]}. The referenced question '
            "must be earlier (lower sort order) in the same section."
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
        _validate_visible_if(self)

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

    if question_type == Question.Type.NUMERIC:
        for key in ("min", "max"):
            if key in config:
                val = config[key]
                if isinstance(val, bool) or not isinstance(val, (int, float)):
                    raise ValidationError(
                        {"config": f"numeric {key!r} must be a number."}
                    )
        low = config.get("min")
        high = config.get("max")
        if low is not None and high is not None and low >= high:
            raise ValidationError(
                {"config": "numeric 'min' must be less than 'max'."}
            )
        if "integer" in config and not isinstance(config["integer"], bool):
            raise ValidationError(
                {"config": "numeric 'integer' must be true or false."}
            )
        if "unit" in config and not isinstance(config["unit"], str):
            raise ValidationError({"config": "numeric 'unit' must be a string."})
        return

    if question_type == Question.Type.MATRIX:
        rows = config.get("rows")
        columns = config.get("columns")
        if (
            not isinstance(rows, list)
            or not rows
            or not all(isinstance(r, str) and r for r in rows)
        ):
            raise ValidationError(
                {"config": "matrix questions require a non-empty 'rows' list of strings."}
            )
        if len(set(rows)) != len(rows):
            raise ValidationError({"config": "matrix 'rows' must be distinct."})
        if (
            not isinstance(columns, list)
            or not columns
            or not all(isinstance(c, str) and c for c in columns)
        ):
            raise ValidationError(
                {"config": "matrix questions require a non-empty 'columns' list of strings."}
            )
        return

    if question_type == Question.Type.RANKING:
        items = config.get("items")
        if (
            not isinstance(items, list)
            or len(items) < 2
            or not all(isinstance(i, str) and i for i in items)
        ):
            raise ValidationError(
                {"config": "ranking questions require an 'items' list of at least two strings."}
            )
        if len(set(items)) != len(items):
            raise ValidationError({"config": "ranking 'items' must be distinct."})
        return

    raise ValidationError({"type": f"unknown question type: {question_type!r}"})


def _validate_visible_if(question: "Question") -> None:
    """Validate a question's skip-logic rule (see experiments.branching)."""
    cond = question.visible_if or {}
    if not cond:
        return
    if not isinstance(cond, dict):
        raise ValidationError({"visible_if": "visible_if must be a JSON object."})
    if "all" in cond and "any" in cond:
        raise ValidationError(
            {"visible_if": "Use only one of 'all' or 'any', not both."}
        )
    clauses = list(iter_clauses(cond))
    if not clauses:
        raise ValidationError({"visible_if": "visible_if has no clauses."})
    for clause in clauses:
        if not isinstance(clause, dict):
            raise ValidationError({"visible_if": "Each clause must be a JSON object."})
        op = clause.get("op")
        if op not in OPERATORS:
            raise ValidationError(
                {"visible_if": f"Unknown operator {op!r}; allowed: {sorted(OPERATORS)}."}
            )
        if op not in VALUELESS_OPS and "value" not in clause:
            raise ValidationError(
                {"visible_if": f"Operator {op!r} requires a 'value'."}
            )
        ref_id = clause.get("question")
        if not isinstance(ref_id, int):
            raise ValidationError(
                {"visible_if": "Each clause needs an integer 'question' id."}
            )
        if not question.experiment_id:
            continue
        ref = Question.objects.filter(pk=ref_id).first()
        if ref is None or ref.experiment_id != question.experiment_id:
            raise ValidationError(
                {
                    "visible_if": (
                        f"Clause references question {ref_id}, which is not in "
                        "this experiment."
                    )
                }
            )
        if question.pk and ref.pk == question.pk:
            raise ValidationError(
                {"visible_if": "A question cannot depend on itself."}
            )
        if ref.section != question.section:
            raise ValidationError(
                {
                    "visible_if": (
                        "A condition may only reference a question in the same "
                        "section."
                    )
                }
            )
        if ref.sort_order >= question.sort_order:
            raise ValidationError(
                {
                    "visible_if": (
                        "The controlling question must come earlier (a lower "
                        "sort order than this question)."
                    )
                }
            )


# --- Prompt ------------------------------------------------------------------


def _prompt_upload_path(instance: "Prompt", filename: str) -> str:
    experiment_id = instance.experiment_id or "unassigned"
    return f"prompts/{experiment_id}/{filename}"


class Prompt(models.Model):
    """An audio prompt shared by the stimuli of a single ``prompt_group``.

    Used exclusively by ``PAIRWISE_AUDIO`` experiments: participants listen
    to the prompt (e.g. the first 4 measures of a song) before comparing
    the two continuations. Looked up by ``(experiment, prompt_group)``.
    """

    experiment = models.ForeignKey(
        Experiment,
        on_delete=models.CASCADE,
        related_name="prompts",
    )
    prompt_group = models.CharField(max_length=200, db_index=True)
    title = models.CharField(max_length=200, blank=True)
    description = models.TextField(blank=True)
    audio = models.FileField(
        upload_to=_prompt_upload_path,
        validators=[audio_extension_validator(), audio_size_validator],
    )
    sha256 = models.CharField(max_length=64, blank=True, editable=False)
    duration_seconds = models.FloatField(null=True, blank=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["experiment", "prompt_group"],
                name="prompt_unique_per_group",
            ),
        ]
        ordering = ("experiment", "prompt_group")

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.title or self.prompt_group

    def clean(self):
        super().clean()
        _ensure_draft(self.experiment if self.experiment_id else None)

    def delete(self, *args, **kwargs):
        _ensure_draft(self.experiment if self.experiment_id else None)
        return super().delete(*args, **kwargs)

    def save(self, *args, **kwargs):
        if not self.sha256 and self.audio:
            try:
                self.audio.open("rb")
                self.audio.seek(0)
                hasher = hashlib.sha256()
                for chunk in iter(lambda: self.audio.read(65536), b""):
                    hasher.update(chunk)
                self.sha256 = hasher.hexdigest()
            except Exception:
                pass
            finally:
                try:
                    self.audio.seek(0)
                except Exception:
                    pass

        super().save(*args, **kwargs)

        if self.duration_seconds is None and self.audio:
            duration = _safe_duration_seconds(
                self.audio.path if _has_path(self.audio) else None
            )
            if duration is not None:
                type(self).objects.filter(pk=self.pk).update(duration_seconds=duration)
                self.duration_seconds = duration
