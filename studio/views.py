"""Researcher-facing studio dashboard.

Server-rendered, permission-gated views for studies a user owns or
collaborates on. The heavy lifting (stats, charts, CSV, ZIP) is delegated to
the existing ``experiments`` modules — this app only adds discovery, creation,
access management and a non-admin home for results.

Structural authoring of conditions/stimuli/questions still happens in the
(ownership-gated) Django admin and is linked out from the study overview for
staff users; a native studio editor is a later epic.
"""
from __future__ import annotations

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.mail import send_mail
from django.db import transaction
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.text import slugify

from accounts import services
from accounts.forms import InviteForm
from accounts.models import Invitation, Membership
from accounts.permissions import can_manage, can_view, role_for
from accounts.roles import Role
from experiments.charts import mean_ratings_svg, pairwise_win_rates_svg
from experiments.csv_exports import (
    answers_csv_response,
    demographics_csv_response,
    pairwise_answers_csv_response,
)
from experiments.exports import build_experiment_archive
from experiments.models import Experiment
from experiments.stats import (
    bradley_terry_analysis,
    experiment_counts,
    mean_listen_duration_ms,
    pairwise_experiment_stats,
    per_stimulus_mean_ratings,
)

from .forms import StudyCreateForm

User = get_user_model()


def _experiment_or_404(request, slug):
    """Fetch an experiment the user may view; otherwise 404 to avoid leaking
    the existence of studies they don't collaborate on."""
    experiment = get_object_or_404(Experiment, slug=slug)
    if not can_view(request.user, experiment):
        raise Http404
    return experiment


def _unique_slug(name: str) -> str:
    base = slugify(name)[:200] or "study"
    slug = base
    i = 2
    while Experiment.objects.filter(slug=slug).exists():
        slug = f"{base[:190]}-{i}"
        i += 1
    return slug


@login_required
def studies(request):
    ids = set(
        Membership.objects.filter(user=request.user).values_list(
            "experiment_id", flat=True
        )
    )
    ids |= set(
        Experiment.objects.filter(owner=request.user).values_list(
            "id", flat=True
        )
    )
    experiments = (
        Experiment.objects.filter(pk__in=ids)
        .select_related("owner")
        .order_by("-created_at")
    )
    rows = [
        {"experiment": exp, "role": role_for(request.user, exp)}
        for exp in experiments
    ]
    return render(request, "studio/studies_list.html", {"rows": rows})


@login_required
def study_create(request):
    if request.method == "POST":
        form = StudyCreateForm(request.POST)
        if form.is_valid():
            with transaction.atomic():
                experiment = form.save(commit=False)
                experiment.slug = _unique_slug(experiment.name)
                experiment.owner = request.user
                experiment.save()
                services.grant_owner_membership(
                    experiment, request.user, actor=request.user
                )
            messages.success(request, f"Created '{experiment.name}'.")
            return redirect("studio:study_overview", slug=experiment.slug)
    else:
        form = StudyCreateForm()
    return render(request, "studio/study_form.html", {"form": form})


@login_required
def study_overview(request, slug):
    experiment = _experiment_or_404(request, slug)
    role = role_for(request.user, experiment)
    can_edit = role in (Role.OWNER, Role.EDITOR)
    context = {
        "experiment": experiment,
        "role": role,
        "can_manage": can_manage(request.user, experiment),
        "can_edit": can_edit,
        # Structural editing lives in the admin, which requires staff access.
        "show_admin_edit": can_edit and request.user.is_staff,
        "admin_change_url": reverse(
            "admin:experiments_experiment_change", args=[experiment.pk]
        ),
        "survey_url": reverse("survey:consent", kwargs={"slug": experiment.slug}),
        "counts": experiment_counts(experiment),
        "mean_listen_ms": mean_listen_duration_ms(experiment),
    }
    if experiment.is_pairwise:
        context["pairwise_stats"] = pairwise_experiment_stats(experiment)
        context["bt_stats"] = bradley_terry_analysis(experiment)
    else:
        context["per_stimulus"] = per_stimulus_mean_ratings(experiment)
    return render(request, "studio/study_overview.html", context)


def _access_context(request, experiment, invite_form):
    memberships = (
        Membership.objects.filter(experiment=experiment)
        .select_related("user")
        .order_by("role", "user__username")
    )
    pending = [inv for inv in experiment.invitations.all() if inv.is_pending()]
    for inv in pending:
        inv.accept_url = request.build_absolute_uri(
            reverse("accounts:invite_accept", kwargs={"token": inv.token})
        )
    transfer_candidates = [
        m.user for m in memberships if m.user_id != experiment.owner_id
    ]
    return {
        "experiment": experiment,
        "memberships": memberships,
        "pending_invitations": pending,
        "invite_form": invite_form,
        "role_choices": [(Role.EDITOR, "Editor"), (Role.VIEWER, "Viewer")],
        "transfer_candidates": transfer_candidates,
        "owner_id": experiment.owner_id,
    }


@login_required
def study_access(request, slug):
    experiment = _experiment_or_404(request, slug)
    if not can_manage(request.user, experiment):
        raise PermissionDenied
    if request.method == "POST":
        return _handle_access_post(request, experiment)
    return render(
        request,
        "studio/study_access.html",
        _access_context(request, experiment, InviteForm()),
    )


def _handle_access_post(request, experiment):
    action = request.POST.get("action")

    if action == "invite":
        form = InviteForm(request.POST)
        if form.is_valid():
            invitation = services.invite_member(
                experiment,
                form.cleaned_data["email"],
                form.cleaned_data["role"],
                actor=request.user,
                request=request,
            )
            link = request.build_absolute_uri(
                reverse(
                    "accounts:invite_accept", kwargs={"token": invitation.token}
                )
            )
            send_mail(
                subject=f"You're invited to collaborate on '{experiment.name}'",
                message=(
                    f"You've been invited as {invitation.role} on the webeval "
                    f"study '{experiment.name}'.\n\n"
                    f"Accept your invitation:\n{link}\n\n"
                    f"This link expires on {invitation.expires_at:%Y-%m-%d}."
                ),
                from_email=None,
                recipient_list=[invitation.email],
                fail_silently=True,
            )
            messages.success(
                request,
                f"Invitation sent to {invitation.email}. If the email doesn't "
                f"arrive, share this link: {link}",
            )
        else:
            messages.error(request, "Enter a valid email and role.")
        return redirect("studio:study_access", slug=experiment.slug)

    if action == "change_role":
        membership = get_object_or_404(
            Membership,
            pk=request.POST.get("membership_id"),
            experiment=experiment,
        )
        new_role = request.POST.get("role")
        if membership.user_id == experiment.owner_id:
            messages.error(
                request, "Transfer ownership to change the owner's role."
            )
        elif new_role in (Role.EDITOR, Role.VIEWER):
            services.change_role(
                membership, new_role, actor=request.user, request=request
            )
            messages.success(request, "Role updated.")
        return redirect("studio:study_access", slug=experiment.slug)

    if action == "remove":
        membership = get_object_or_404(
            Membership,
            pk=request.POST.get("membership_id"),
            experiment=experiment,
        )
        if membership.user_id == experiment.owner_id:
            messages.error(
                request, "You can't remove the owner. Transfer ownership first."
            )
        else:
            services.remove_member(
                membership, actor=request.user, request=request
            )
            messages.success(request, "Collaborator removed.")
        return redirect("studio:study_access", slug=experiment.slug)

    if action == "revoke":
        invitation = get_object_or_404(
            Invitation,
            pk=request.POST.get("invitation_id"),
            experiment=experiment,
        )
        services.revoke_invitation(
            invitation, actor=request.user, request=request
        )
        messages.success(request, "Invitation revoked.")
        return redirect("studio:study_access", slug=experiment.slug)

    if action == "transfer":
        new_owner = get_object_or_404(User, pk=request.POST.get("new_owner_id"))
        if not Membership.objects.filter(
            experiment=experiment, user=new_owner
        ).exists():
            messages.error(
                request, "Choose an existing collaborator to transfer to."
            )
        else:
            services.transfer_ownership(
                experiment, new_owner, actor=request.user, request=request
            )
            messages.success(
                request,
                f"Ownership transferred to {new_owner.get_username()}.",
            )
        return redirect("studio:study_access", slug=experiment.slug)

    raise PermissionDenied


# --- result/export pass-throughs (reuse experiments builders) ---------------


def _exclude_flagged(request) -> bool:
    return request.GET.get("exclude_flagged") in ("1", "true", "on")


@login_required
def answers_csv(request, slug):
    return answers_csv_response(
        _experiment_or_404(request, slug), exclude_flagged=_exclude_flagged(request)
    )


@login_required
def demographics_csv(request, slug):
    return demographics_csv_response(
        _experiment_or_404(request, slug), exclude_flagged=_exclude_flagged(request)
    )


@login_required
def pairwise_csv(request, slug):
    return pairwise_answers_csv_response(_experiment_or_404(request, slug))


@login_required
def export_zip(request, slug):
    experiment = _experiment_or_404(request, slug)
    payload = build_experiment_archive(experiment)
    response = HttpResponse(payload, content_type="application/zip")
    response["Content-Disposition"] = (
        f'attachment; filename="{experiment.slug}.zip"'
    )
    response["Content-Length"] = str(len(payload))
    return response


@login_required
def chart(request, slug):
    experiment = _experiment_or_404(request, slug)
    svg = (
        pairwise_win_rates_svg(experiment)
        if experiment.is_pairwise
        else mean_ratings_svg(experiment)
    )
    return HttpResponse(svg, content_type="image/svg+xml")
