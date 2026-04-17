from __future__ import annotations

from django import forms
from django.utils import timezone

from .scopes import SCOPES

_INPUT_CLASS = (
    "block w-full rounded-default border border-base-200 dark:border-base-700 "
    "bg-white dark:bg-base-900 px-3 py-2 text-sm"
)


class CreateAPIKeyForm(forms.Form):
    name = forms.CharField(
        max_length=100,
        help_text="A label to recognise this key later (e.g. 'CI upload bot').",
        widget=forms.TextInput(attrs={"class": _INPUT_CLASS}),
    )
    scopes = forms.MultipleChoiceField(
        choices=sorted(SCOPES.items()),
        widget=forms.CheckboxSelectMultiple,
        help_text="Grant only the scopes this key actually needs.",
    )
    expires_at = forms.DateTimeField(
        required=False,
        widget=forms.DateTimeInput(
            attrs={"type": "datetime-local", "class": _INPUT_CLASS}
        ),
        help_text="Optional. Leave blank for no expiry.",
    )

    def clean_expires_at(self):
        value = self.cleaned_data.get("expires_at")
        if value is not None and value <= timezone.now():
            raise forms.ValidationError("Expiry must be in the future.")
        return value
