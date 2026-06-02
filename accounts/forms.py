"""Forms for the accounts app."""
from __future__ import annotations

from django import forms
from django.contrib.auth.forms import UserCreationForm

from .models import Profile
from .roles import Role


class RegisterForm(UserCreationForm):
    email = forms.EmailField(required=True)

    class Meta(UserCreationForm.Meta):
        fields = ("username", "email")

    def save(self, commit=True):
        user = super().save(commit=False)
        user.email = self.cleaned_data["email"]
        if commit:
            user.save()
        return user


class InviteForm(forms.Form):
    email = forms.EmailField()
    # Owners are never invited — ownership is transferred, not granted.
    role = forms.ChoiceField(
        choices=[(Role.EDITOR, "Editor"), (Role.VIEWER, "Viewer")],
        initial=Role.EDITOR,
    )


class ProfileForm(forms.ModelForm):
    class Meta:
        model = Profile
        fields = ("display_name", "preferred_language")
