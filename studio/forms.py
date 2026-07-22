"""Forms for the studio dashboard."""
from __future__ import annotations

from django import forms
from django.core.exceptions import ValidationError

from experiments.models import Condition, Experiment, Stimulus


class StudyCreateForm(forms.ModelForm):
    class Meta:
        model = Experiment
        fields = ("name", "description", "mode")
        widgets = {"description": forms.Textarea(attrs={"rows": 3})}


class ConditionForm(forms.ModelForm):
    """Create/edit a condition inside one study.

    The view binds ``instance.experiment`` before validation so the
    ``unique_together("experiment", "name")`` constraint is checked.
    """

    class Meta:
        model = Condition
        fields = ("name", "description")
        widgets = {"description": forms.Textarea(attrs={"rows": 2})}

    def validate_unique(self):
        # ``experiment`` is not a form field, so ModelForm would exclude it
        # from uniqueness checks and a duplicate name would surface as an
        # IntegrityError instead of a friendly form error. The view always
        # binds instance.experiment, so include it.
        exclude = self._get_validation_exclusions()
        exclude.discard("experiment")
        try:
            self.instance.validate_unique(exclude=exclude)
        except ValidationError as e:
            self._update_errors(e)


class StimulusForm(forms.ModelForm):
    """Create/edit a stimulus of any kind inside one study.

    Validation is delegated to the model: the ``FileField`` validators check
    extension/size, and ``Stimulus.clean()`` enforces the per-kind field
    matrix plus the draft-only lock — a ModelForm surfaces both as errors.
    """

    class Meta:
        model = Stimulus
        fields = (
            "condition",
            "title",
            "description",
            "kind",
            "audio",
            "image",
            "video",
            "text_body",
            "embed_url",
            "prompt_group",
            "is_active",
            "sort_order",
        )
        widgets = {
            "description": forms.Textarea(attrs={"rows": 2}),
            "text_body": forms.Textarea(attrs={"rows": 6}),
        }

    def __init__(self, *args, experiment=None, **kwargs):
        super().__init__(*args, **kwargs)
        if experiment is not None:
            self.fields["condition"].queryset = experiment.conditions.all()
