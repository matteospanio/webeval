"""Registration, profile and invitation-acceptance views."""
from __future__ import annotations

from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from .forms import ProfileForm, RegisterForm
from .models import Invitation, Profile
from .services import accept_invitation


def register(request):
    if not settings.ACCOUNTS_ALLOW_REGISTRATION:
        raise Http404("Registration is disabled.")
    if request.user.is_authenticated:
        return redirect("studio:studies")
    next_url = request.GET.get("next") or request.POST.get("next") or ""
    if request.method == "POST":
        form = RegisterForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            return redirect(next_url or "studio:studies")
    else:
        form = RegisterForm()
    return render(
        request, "accounts/register.html", {"form": form, "next": next_url}
    )


@login_required
def profile(request):
    prof, _ = Profile.objects.get_or_create(user=request.user)
    if request.method == "POST":
        form = ProfileForm(request.POST, instance=prof)
        if form.is_valid():
            form.save()
            messages.success(request, "Profile updated.")
            return redirect("accounts:profile")
    else:
        form = ProfileForm(instance=prof)
    return render(request, "accounts/profile.html", {"form": form})


def invite_accept(request, token):
    invitation = get_object_or_404(Invitation, token=token)
    if not invitation.is_pending():
        return render(
            request,
            "accounts/invite_invalid.html",
            {"invitation": invitation},
            status=410,
        )
    if not request.user.is_authenticated:
        target = reverse("accounts:invite_accept", kwargs={"token": token})
        return redirect(
            f"{reverse('accounts:login')}?{urlencode({'next': target})}"
        )
    if request.method == "POST":
        accept_invitation(invitation, request.user, request=request)
        messages.success(
            request,
            f"You now have {invitation.role} access to "
            f"'{invitation.experiment.name}'.",
        )
        return redirect(
            "studio:study_overview", slug=invitation.experiment.slug
        )
    return render(
        request, "accounts/invite_accept.html", {"invitation": invitation}
    )
