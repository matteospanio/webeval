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

from .components import (
    available_question_components,
    get_question_component,
    is_question_component,
)
from .models import Question, QuestionTemplate


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

    # Numeric-specific helpers.
    numeric_min = forms.FloatField(
        required=False, help_text="Optional minimum allowed value."
    )
    numeric_max = forms.FloatField(
        required=False, help_text="Optional maximum allowed value."
    )
    numeric_integer = forms.BooleanField(
        required=False, help_text="Require a whole number (no decimals)."
    )
    numeric_unit = forms.CharField(
        required=False,
        help_text="Optional unit shown next to the input (e.g. 'years').",
    )

    # Matrix-specific helpers.
    matrix_rows = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 5}),
        help_text="One row label per line (the sub-questions / items being rated).",
    )
    matrix_columns = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 5}),
        help_text="One column label per line (the shared answer scale).",
    )

    # Ranking-specific helpers.
    ranking_items = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 5}),
        help_text="One item per line. Participants assign each a unique rank.",
    )

    # Raw config for custom (plugin) question types — see experiments.components.
    plugin_config = forms.JSONField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 6}),
        help_text=(
            "Used only for custom (plugin) question types: the raw JSON config, "
            'e.g. {"items": ["A", "B"], "total": 100}. The component validates '
            "its own shape."
        ),
    )

    class Meta:
        model = Question
        exclude = ("config",)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Offer registered plugin components in the type dropdown.
        extra = [
            (c.type, f"{c.label} (plugin)") for c in available_question_components()
        ]
        if extra and "type" in self.fields:
            self.fields["type"].choices = list(self.fields["type"].choices) + extra
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
        elif t == Question.Type.NUMERIC:
            self.fields["numeric_min"].initial = cfg.get("min")
            self.fields["numeric_max"].initial = cfg.get("max")
            self.fields["numeric_integer"].initial = bool(cfg.get("integer", False))
            self.fields["numeric_unit"].initial = cfg.get("unit", "")
        elif t == Question.Type.MATRIX:
            rows = cfg.get("rows") or []
            cols = cfg.get("columns") or []
            if isinstance(rows, list):
                self.fields["matrix_rows"].initial = "\n".join(str(r) for r in rows)
            if isinstance(cols, list):
                self.fields["matrix_columns"].initial = "\n".join(
                    str(c) for c in cols
                )
        elif t == Question.Type.RANKING:
            items = cfg.get("items") or []
            if isinstance(items, list):
                self.fields["ranking_items"].initial = "\n".join(
                    str(i) for i in items
                )
        elif is_question_component(t):
            self.fields["plugin_config"].initial = cfg

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
        elif question_type == Question.Type.NUMERIC:
            low = cleaned.get("numeric_min")
            high = cleaned.get("numeric_max")
            if low is not None:
                config["min"] = low
            if high is not None:
                config["max"] = high
            if cleaned.get("numeric_integer"):
                config["integer"] = True
            unit = (cleaned.get("numeric_unit") or "").strip()
            if unit:
                config["unit"] = unit
            if low is not None and high is not None and low >= high:
                self.add_error(
                    "numeric_max", "Maximum must be greater than minimum."
                )
                config = {}
        elif question_type == Question.Type.MATRIX:
            rows = [
                line.strip()
                for line in (cleaned.get("matrix_rows") or "").splitlines()
                if line.strip()
            ]
            cols = [
                line.strip()
                for line in (cleaned.get("matrix_columns") or "").splitlines()
                if line.strip()
            ]
            if not rows:
                self.add_error("matrix_rows", "Enter at least one row (one per line).")
            elif len(set(rows)) != len(rows):
                self.add_error("matrix_rows", "Row labels must be distinct.")
            if not cols:
                self.add_error(
                    "matrix_columns", "Enter at least one column (one per line)."
                )
            if rows and cols and len(set(rows)) == len(rows):
                config = {"rows": rows, "columns": cols}
        elif question_type == Question.Type.RANKING:
            items = [
                line.strip()
                for line in (cleaned.get("ranking_items") or "").splitlines()
                if line.strip()
            ]
            if len(items) < 2:
                self.add_error(
                    "ranking_items", "Enter at least two items (one per line)."
                )
            elif len(set(items)) != len(items):
                self.add_error("ranking_items", "Items must be distinct.")
            else:
                config = {"items": items}
        elif is_question_component(question_type):
            raw = cleaned.get("plugin_config") or {}
            if not isinstance(raw, dict):
                self.add_error("plugin_config", "Config must be a JSON object.")
            else:
                try:
                    get_question_component(question_type).validate_config(raw)
                    config = raw
                except ValidationError as exc:
                    self.add_error("plugin_config", exc.messages)

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


class QuestionTemplateAdminForm(forms.ModelForm):
    """Question-bank template form: same type dropdown as questions.

    The stock ModelForm only offers ``Question.Type.choices``, so a template
    saved from a plugin-type question (via "Save to my question bank") would
    render unselected and refuse to save. Mirror ``QuestionAdminForm`` by
    appending every registered component to the choices.
    """

    class Meta:
        model = QuestionTemplate
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        extra = [
            (c.type, f"{c.label} (plugin)") for c in available_question_components()
        ]
        if extra and "type" in self.fields:
            self.fields["type"].choices = list(self.fields["type"].choices) + extra
