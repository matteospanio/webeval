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

import json

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.mail import send_mail
from django.db import transaction
from django.http import Http404, HttpResponse, HttpResponseNotAllowed, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.text import slugify

from accounts import services
from accounts.forms import InviteForm
from accounts.models import Invitation, Membership
from accounts.permissions import can_edit, can_manage, can_view, role_for
from accounts.roles import Role
from experiments.analysis import (
    analyse_question,
    available_segments,
    segmented_question_analysis,
)
from experiments.charts import mean_ratings_svg, pairwise_win_rates_svg
from experiments.cloning import clone_experiment
from experiments.components import available_question_components
from experiments.stats_tests import compare_conditions
from experiments.csv_exports import (
    answers_csv_response,
    completion_codes_csv_response,
    demographics_csv_response,
    pairwise_answers_csv_response,
)
from experiments.exports import build_experiment_archive
from experiments.models import Experiment, Question
from experiments.readiness import readiness_problems
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


def _question_results(experiment):
    """Per-question analysis + (where applicable) a condition comparison."""
    results = []
    for q in experiment.questions.all().order_by("section", "sort_order", "id"):
        comparison = None
        if q.section == Question.Section.STIMULUS:
            comp = compare_conditions(experiment, q)
            if comp.get("applicable"):
                comparison = comp
        results.append({"analysis": analyse_question(experiment, q), "comparison": comparison})
    return results


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
        "follows": experiment.follows,
        "next_phases": list(experiment.next_phases.all()),
        "is_live": experiment.state == Experiment.State.ACTIVE,
        "readiness_problems": readiness_problems(experiment),
        "segment_options": available_segments(),
    }
    segment = request.GET.get("segment")
    if segment in dict(available_segments()):
        context["segment"] = segment
        context["segmented_results"] = segmented_question_analysis(experiment, segment)
    else:
        context["question_results"] = _question_results(experiment)
    if experiment.is_pairwise:
        context["pairwise_stats"] = pairwise_experiment_stats(experiment)
        context["bt_stats"] = bradley_terry_analysis(experiment)
    else:
        context["per_stimulus"] = per_stimulus_mean_ratings(experiment)
    return render(request, "studio/study_overview.html", context)


@login_required
def study_clone(request, slug):
    """Duplicate a study the user can view into a fresh DRAFT they own."""
    experiment = _experiment_or_404(request, slug)
    if request.method != "POST":
        return redirect("studio:study_overview", slug=experiment.slug)
    with transaction.atomic():
        clone = clone_experiment(experiment, owner=request.user)
        services.grant_owner_membership(clone, request.user, actor=request.user)
    messages.success(
        request,
        f"Duplicated '{experiment.name}' as a new draft you own. "
        "Edit it, then activate when ready.",
    )
    return redirect("studio:study_overview", slug=clone.slug)


# --- drag-&-drop question builder ------------------------------------------


_BUILTIN_DEFAULT_CONFIG = {
    "rating": {"min": 0, "max": 100, "step": 1},
    "choice": {"choices": ["Option 1", "Option 2"], "multi": False},
    "text": {"max_length": 500},
    "likert": {
        "steps": 5,
        "labels": ["Strongly disagree", "Disagree", "Neutral", "Agree", "Strongly agree"],
    },
    "numeric": {},
    "matrix": {"rows": ["Row 1"], "columns": ["Column 1", "Column 2"]},
    "ranking": {"items": ["Item 1", "Item 2"]},
}


def _palette() -> list[dict]:
    """Question types offered in the builder: built-ins + registered plugins."""
    palette = [
        {
            "type": value,
            "label": label,
            "plugin": False,
            "default_config": _BUILTIN_DEFAULT_CONFIG.get(value, {}),
        }
        for value, label in Question.Type.choices
    ]
    palette += [
        {
            "type": comp.type,
            "label": comp.label,
            "plugin": True,
            "default_config": comp.default_config(),
        }
        for comp in available_question_components()
    ]
    return palette


def _serialize_question(q: Question) -> dict:
    return {
        "id": q.pk,
        "section": q.section,
        "type": q.type,
        "prompt": q.prompt,
        "required": q.required,
        "page_break_before": q.page_break_before,
        "show_prompt": q.show_prompt,
        "config": q.config or {},
    }


@login_required
def study_build(request, slug):
    """The drag-&-drop question builder for a draft study."""
    experiment = _experiment_or_404(request, slug)
    if not can_edit(request.user, experiment):
        raise PermissionDenied
    questions = [
        _serialize_question(q)
        for q in experiment.questions.all().order_by("section", "sort_order", "id")
    ]
    return render(
        request,
        "studio/study_build.html",
        {
            "experiment": experiment,
            "is_draft": experiment.state == Experiment.State.DRAFT,
            "questions": questions,
            "palette": _palette(),
            "save_url": reverse("studio:study_build_save", kwargs={"slug": slug}),
        },
    )


def _question_errors(exc: ValidationError) -> dict:
    if hasattr(exc, "message_dict"):
        return exc.message_dict
    return {"__all__": exc.messages}


@login_required
def study_build_save(request, slug):
    """Persist the builder's question set (create / update / delete + reorder)."""
    experiment = _experiment_or_404(request, slug)
    if not can_edit(request.user, experiment):
        raise PermissionDenied
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    if experiment.state != Experiment.State.DRAFT:
        return JsonResponse(
            {"ok": False, "error": "Questions can only be edited while the study is a draft."},
            status=409,
        )
    try:
        items = json.loads(request.body or "{}")["questions"]
        if not isinstance(items, list):
            raise ValueError
    except (ValueError, KeyError, TypeError):
        return JsonResponse({"ok": False, "error": "Invalid payload."}, status=400)

    with transaction.atomic():
        existing = {q.pk: q for q in experiment.questions.all()}
        errors: dict[str, dict] = {}
        prepared: list[Question] = []
        for idx, item in enumerate(items):
            qid = item.get("id")
            q = existing.get(qid) if qid else None
            if q is None:
                q = Question(experiment=experiment)
            q.section = item.get("section") or Question.Section.STIMULUS
            q.type = item.get("type") or ""
            q.prompt = item.get("prompt") or ""
            q.required = bool(item.get("required", True))
            q.page_break_before = bool(item.get("page_break_before", False))
            q.show_prompt = bool(item.get("show_prompt", False))
            q.config = item.get("config") if isinstance(item.get("config"), dict) else {}
            q.sort_order = idx
            try:
                q.full_clean()
            except ValidationError as exc:
                errors[str(idx)] = _question_errors(exc)
            prepared.append(q)

        if errors:
            transaction.set_rollback(True)
            return JsonResponse({"ok": False, "errors": errors}, status=400)

        kept_ids = set()
        for q in prepared:
            q.save()
            kept_ids.add(q.pk)
        for pk, q in existing.items():
            if pk not in kept_ids:
                q.delete()

    # ids are returned in posted order so the builder can adopt them on the
    # new cards (a second save then updates instead of re-creating).
    return JsonResponse(
        {"ok": True, "count": len(prepared), "ids": [q.pk for q in prepared]}
    )


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
def completion_codes_csv(request, slug):
    return completion_codes_csv_response(_experiment_or_404(request, slug))


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
