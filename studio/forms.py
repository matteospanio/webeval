"""Forms for the studio dashboard."""
from __future__ import annotations

from django import forms

from experiments.models import Experiment


class StudyCreateForm(forms.ModelForm):
    class Meta:
        model = Experiment
        fields = ("name", "description", "mode")
        widgets = {"description": forms.Textarea(attrs={"rows": 3})}
