"""Admin forms for the experiments app.

The :class:`~experiments.models.Question` model stores per-type settings
(rating min/max/step, choice options, text max length) in a single
``config`` JSONField so the flow, exports, and validators can treat every
question uniformly. Editing that JSONField directly in the admin means
typing raw JSON into a textarea, which is fragile (a stray comma crashes
the save) and opaque to researchers setting up a study.

:class:`QuestionAdminForm` hides the raw ``config`` field and exposes one
flat helper field per underlying setting. On ``clean()`` it assembles the
helper-field values into the dict shape the model expects and sets it on
the instance, so the storage format and the downstream code stay
completely unchanged — only the admin widget is different.
"""
from __future__ import annotations

from django import forms
from django.core.exceptions import ValidationError
from django.forms.models import construct_instance
from django.forms.models import InlineForeignKeyField

from .models import Question


class QuestionAdminForm(forms.ModelForm):
    # Rating-specific helpers.
    rating_min = forms.IntegerField(
        required=False,
        help_text="Slider minimum value (e.g. 0).",
    )
    rating_max = forms.IntegerField(
        required=False,
        help_text="Slider maximum value (e.g. 100).",
    )
    rating_step = forms.IntegerField(
        required=False,
        min_value=1,
        help_text="Slider increment (must be a positive integer).",
    )
    rating_min_label = forms.CharField(
        required=False,
        help_text="Optional label for the low end of the scale (e.g. 'Not at all').",
    )
    rating_max_label = forms.CharField(
        required=False,
        help_text="Optional label for the high end of the scale (e.g. 'Very much').",
    )

    # Choice-specific helpers.
    choice_options = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 5}),
        help_text="One choice per line. Leading/trailing whitespace is trimmed.",
    )
    choice_multi = forms.BooleanField(
        required=False,
        help_text="Allow participants to select more than one option.",
    )

    # Text-specific helpers.
    text_max_length = forms.IntegerField(
        required=False,
        min_value=1,
        help_text="Maximum number of characters for a free-text answer.",
    )

    # Likert-specific helpers.
    likert_steps = forms.IntegerField(
        required=False,
        min_value=2,
        max_value=11,
        help_text="Number of points on the scale (2..11).",
    )
    likert_labels = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 5}),
        help_text="One label per line, matching the number of steps.",
    )

    class Meta:
        model = Question
        exclude = ("config",)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            self._populate_from_instance()

    def _populate_from_instance(self) -> None:
        cfg = self.instance.config or {}
        if not isinstance(cfg, dict):
            return
        t = self.instance.type
        if t == Question.Type.RATING:
            self.fields["rating_min"].initial = cfg.get("min")
            self.fields["rating_max"].initial = cfg.get("max")
            self.fields["rating_step"].initial = cfg.get("step")
            self.fields["rating_min_label"].initial = cfg.get("min_label", "")
            self.fields["rating_max_label"].initial = cfg.get("max_label", "")
        elif t == Question.Type.CHOICE:
            choices = cfg.get("choices") or []
            if isinstance(choices, list):
                self.fields["choice_options"].initial = "\n".join(
                    str(c) for c in choices
                )
            self.fields["choice_multi"].initial = bool(cfg.get("multi", False))
        elif t == Question.Type.TEXT:
            self.fields["text_max_length"].initial = cfg.get("max_length")
        elif t == Question.Type.LIKERT:
            self.fields["likert_steps"].initial = cfg.get("steps")
            labels = cfg.get("labels") or []
            if isinstance(labels, list):
                self.fields["likert_labels"].initial = "\n".join(
                    str(lb) for lb in labels
                )

    def clean(self):
        cleaned = super().clean()
        question_type = cleaned.get("type")
        config: dict = {}

        if question_type == Question.Type.RATING:
            for field in ("rating_min", "rating_max", "rating_step"):
                if cleaned.get(field) is None:
                    self.add_error(field, "Required for rating questions.")
            if not self.errors:
                config = {
                    "min": cleaned["rating_min"],
                    "max": cleaned["rating_max"],
                    "step": cleaned["rating_step"],
                }
                min_label = (cleaned.get("rating_min_label") or "").strip()
                max_label = (cleaned.get("rating_max_label") or "").strip()
                if min_label:
                    config["min_label"] = min_label
                if max_label:
                    config["max_label"] = max_label
        elif question_type == Question.Type.CHOICE:
            raw = cleaned.get("choice_options") or ""
            options = [line.strip() for line in raw.splitlines() if line.strip()]
            if not options:
                self.add_error(
                    "choice_options",
                    "Enter at least one option (one per line).",
                )
            else:
                config = {
                    "choices": options,
                    "multi": bool(cleaned.get("choice_multi")),
                }
        elif question_type == Question.Type.TEXT:
            if cleaned.get("text_max_length") is None:
                self.add_error(
                    "text_max_length",
                    "Required for free-text questions.",
                )
            else:
                config = {"max_length": cleaned["text_max_length"]}
        elif question_type == Question.Type.LIKERT:
            steps = cleaned.get("likert_steps")
            if steps is None:
                self.add_error("likert_steps", "Required for Likert questions.")
            raw_labels = cleaned.get("likert_labels") or ""
            labels = [line.strip() for line in raw_labels.splitlines() if line.strip()]
            if steps and len(labels) != steps:
                self.add_error(
                    "likert_labels",
                    f"Expected {steps} labels (one per line), got {len(labels)}.",
                )
            elif steps:
                config = {"steps": steps, "labels": labels}

        # Whatever we built, push it onto the instance so the model-level
        # validator in Question.clean() sees the right shape. On errors we
        # still set an empty dict so we don't leak a stale value.
        self.instance.config = config
        return cleaned

    def _post_clean(self):
        # Mirrors Django's ModelForm._post_clean() but filters ``config``
        # out of any model-level ValidationError. Because we hide the raw
        # JSONField behind helper fields, a ``{"config": ...}`` error has
        # no form field to attach to and _update_errors() would raise
        # ValueError. Config problems are already surfaced as helper-field
        # errors in clean(), so dropping the key here is safe.
        opts = self._meta
        exclude = self._get_validation_exclusions()

        for name, field in self.fields.items():
            if isinstance(field, InlineForeignKeyField):
                exclude.add(name)

        try:
            self.instance = construct_instance(
                self, self.instance, opts.fields, opts.exclude
            )
        except ValidationError as e:
            self._update_errors(e)

        try:
            self.instance.full_clean(exclude=exclude, validate_unique=False)
        except ValidationError as e:
            errors = e.error_dict if hasattr(e, "error_dict") else None
            if errors is not None:
                errors.pop("config", None)
                if errors:
                    self._update_errors(ValidationError(errors))
            else:
                self._update_errors(e)

        try:
            self.instance.validate_unique()
        except ValidationError as e:
            self._update_errors(e)
