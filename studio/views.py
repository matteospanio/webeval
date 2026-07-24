"""Researcher-facing studio dashboard.

Server-rendered, permission-gated views for studies a user owns or
collaborates on. The heavy lifting (stats, charts, CSV, ZIP) is delegated to
the existing ``experiments`` modules — this app adds discovery, creation,
authoring (questions via the drag-&-drop builder, conditions & stimuli via
the stimuli pages), the study lifecycle (test/activate/close), access
management, and a non-admin home for results. The Django admin remains a
staff-only power surface; researchers never need it.
"""
from __future__ import annotations

import json

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.mail import send_mail
from django.db import transaction
from django.db.models import Q
from django.http import Http404, HttpResponse, HttpResponseNotAllowed, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.text import slugify

from accounts import services
from accounts.forms import InviteForm
from accounts.models import AuditEvent, Invitation, Membership
from accounts.permissions import (
    can_edit,
    can_manage,
    can_view,
    role_for,
    visible_experiment_ids,
)
from accounts.roles import Role
from experiments.analysis import (
    analyse_question,
    available_segments,
    segmented_question_analysis,
)
from experiments.charts import mean_ratings_svg, pairwise_win_rates_svg
from experiments.cloning import clone_experiment
from experiments.components import available_question_components
from experiments.power import achieved_power, cohens_d, required_n_per_group
from experiments.stats_tests import _grouped_answers, compare_conditions
from experiments.csv_exports import (
    answers_csv_response,
    completion_codes_csv_response,
    demographics_csv_response,
    events_csv_response,
    pairwise_answers_csv_response,
)
from experiments.exports import build_experiment_archive
from experiments.models import Condition, Experiment, Question, Stimulus, Webhook
from experiments.queries import real_responses
from experiments.readiness import readiness_problems
from experiments.stats import (
    bradley_terry_analysis,
    experiment_counts,
    mean_listen_duration_ms,
    pairwise_experiment_stats,
    per_stimulus_mean_ratings,
)
from survey.models import ParticipantSession, Response
from survey.views import _withdraw_data

from .forms import ConditionForm, StimulusForm, StudyCreateForm

User = get_user_model()


def _experiment_or_404(request, slug):
    """Fetch an experiment the user may view; otherwise 404 to avoid leaking
    the existence of studies they don't collaborate on."""
    experiment = get_object_or_404(Experiment, slug=slug)
    if not can_view(request.user, experiment):
        raise Http404
    return experiment


def _study_nav_context(request, experiment) -> dict:
    """Context every per-study page needs for the study sub-navigation
    (``studio/_study_nav.html``): which tabs the user may see."""
    return {
        "experiment": experiment,
        "nav_can_edit": can_edit(request.user, experiment),
        "nav_can_manage": can_manage(request.user, experiment),
    }


def _audit(request, experiment, target, *, action=AuditEvent.Action.EDIT, **extra):
    """Thin wrapper over ``services.record_audit`` that fills in the actor and
    request from the studio request (every studio mutation records one)."""
    services.record_audit(
        experiment, action, actor=request.user, target=target,
        request=request, **extra,
    )


def _unique_slug(name: str) -> str:
    base = slugify(name)[:200] or "study"
    slug = base
    i = 2
    while Experiment.objects.filter(slug=slug).exists():
        slug = f"{base[:190]}-{i}"
        i += 1
    return slug


def _visible_experiments(user):
    """Studies a user may see in the studio, consistent with the object-level
    ``can_view`` decision (``accounts.permissions``): superusers see all;
    everyone else sees owned + collaborating, plus legacy unowned studies for
    staff. Building the list any other way lets a study a user can open never
    appear in their list / compare / DSR."""
    experiments = Experiment.objects.select_related("owner").order_by("-created_at")
    if getattr(user, "is_superuser", False):
        return experiments
    return experiments.filter(pk__in=visible_experiment_ids(user))


@login_required
def studies(request):
    experiments = _visible_experiments(request.user)
    query = (request.GET.get("q") or "").strip()
    if query:
        experiments = experiments.filter(name__icontains=query)
    rows = [
        {"experiment": exp, "role": role_for(request.user, exp)}
        for exp in experiments
    ]
    return render(
        request, "studio/studies_list.html", {"rows": rows, "query": query}
    )


def _dsr_export(request, identifier, sessions):
    data = {"identifier": identifier, "sessions": []}
    for s in sessions:
        data["sessions"].append({
            "experiment": s.experiment.slug,
            "session_id": str(s.id),
            "started_at": s.started_at.isoformat() if s.started_at else None,
            "submitted_at": s.submitted_at.isoformat() if s.submitted_at else None,
            "device_type": s.device_type,
            "country_code": s.country_code,
            "external_id": s.external_id,
            "participant_uid": s.participant_uid,
            "responses": [
                {"question_id": r.question_id, "stimulus_id": r.stimulus_id,
                 "answer": r.get_answer()}
                for r in s.responses.all()
            ],
        })
    services.record_audit(
        None, AuditEvent.Action.EXPORT, actor=request.user,
        target=f"DSR:{identifier}", request=request, sessions=len(sessions),
    )
    resp = JsonResponse(data, json_dumps_params={"indent": 2})
    resp["Content-Disposition"] = f'attachment; filename="dsr-{identifier}.json"'
    return resp


@login_required
def data_subject_request(request):
    """Find, export, or erase a participant's data across the user's studies,
    matched by participant code or external id."""
    identifier = (
        request.POST.get("identifier") or request.GET.get("identifier") or ""
    ).strip()
    visible = list(_visible_experiments(request.user))
    visible_ids = [e.id for e in visible]
    manage_ids = {e.id for e in visible if can_manage(request.user, e)}

    sessions = []
    if identifier:
        sessions = list(
            ParticipantSession.objects.filter(experiment_id__in=visible_ids)
            .filter(Q(participant_uid=identifier) | Q(external_id=identifier))
            .select_related("experiment")
            .order_by("experiment__name", "started_at")
        )

    if request.method == "POST" and identifier:
        action = request.POST.get("action")
        if action == "export":
            return _dsr_export(request, identifier, sessions)
        if action == "delete":
            erased = 0
            for s in sessions:
                if s.experiment_id not in manage_ids:
                    continue  # only erase where the user can manage the study
                _withdraw_data(s)
                services.record_audit(
                    s.experiment, AuditEvent.Action.DELETE, actor=request.user,
                    target=f"DSR:{identifier}", request=request,
                )
                erased += 1
            messages.success(request, f"Erased data for {erased} session(s).")
            return redirect(f"{reverse('studio:dsr')}?identifier={identifier}")

    return render(
        request,
        "studio/dsr.html",
        {"identifier": identifier, "sessions": sessions, "manage_ids": manage_ids},
    )


def _headline_metric(experiment) -> str:
    if experiment.is_pairwise:
        return f"{pairwise_experiment_stats(experiment).total_pairs_shown} pairs"
    rating_rows = per_stimulus_mean_ratings(experiment)
    total_n = sum(r["n"] for r in rating_rows)
    if not total_n:
        return "—"
    weighted = sum(r["mean"] * r["n"] for r in rating_rows) / total_n
    return f"mean {weighted:.1f}"


@login_required
def compare(request):
    """Key metrics for all the user's studies, side by side."""
    rows = []
    for exp in _visible_experiments(request.user):
        counts = experiment_counts(exp)
        rows.append(
            {
                "experiment": exp,
                "counts": counts,
                "responses": real_responses(exp).count(),
                "headline": _headline_metric(exp),
            }
        )
    return render(request, "studio/compare.html", {"rows": rows})


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
        **_study_nav_context(request, experiment),
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


# --- study lifecycle ---------------------------------------------------------

# action name → (allowed current states, target state)
_STATE_ACTIONS = {
    "test": ({Experiment.State.DRAFT}, Experiment.State.TEST),
    "activate": (
        {Experiment.State.DRAFT, Experiment.State.TEST},
        Experiment.State.ACTIVE,
    ),
    "close": (
        {Experiment.State.TEST, Experiment.State.ACTIVE},
        Experiment.State.CLOSED,
    ),
    "reopen": ({Experiment.State.CLOSED}, Experiment.State.ACTIVE),
    "draft": ({Experiment.State.TEST}, Experiment.State.DRAFT),
}


def _activate_from_test(request, experiment):
    """TEST→ACTIVE with the purge-or-promote choice. The data handling is
    shared with the admin activate view via ``data_ops.activate_from_test``;
    the audit trail + messaging stay here."""
    from experiments.data_ops import activate_from_test

    purged = activate_from_test(
        experiment, purge=request.POST.get("test_data") == "purge"
    )
    if purged is not None:
        _audit(request, experiment, "test-phase data",
               action=AuditEvent.Action.PURGE, sessions=purged.sessions)
    _audit(request, experiment, experiment.slug,
           action=AuditEvent.Action.ACTIVATE)
    if purged is not None:
        messages.success(
            request,
            f"Activated '{experiment.name}' and removed the test-phase data "
            f"({purged.sessions} session(s)).",
        )
    else:
        messages.success(
            request, f"Activated '{experiment.name}' — test-phase data was kept."
        )


@login_required
def study_state_change(request, slug, action):
    """Confirm + apply a lifecycle transition (test/activate/close/reopen/
    draft) right in the studio — no Django admin required."""
    experiment = _experiment_or_404(request, slug)
    if not can_manage(request.user, experiment):
        raise PermissionDenied
    if action not in _STATE_ACTIONS:
        raise Http404
    allowed_states, target = _STATE_ACTIONS[action]
    if experiment.state not in allowed_states:
        messages.warning(
            request,
            f"'{experiment.name}' is {experiment.get_state_display().lower()} — "
            f"the '{action}' action does not apply.",
        )
        return redirect("studio:study_overview", slug=slug)

    problems = (
        readiness_problems(experiment)
        if target in (Experiment.State.TEST, Experiment.State.ACTIVE)
        else []
    )
    from_test = experiment.state == Experiment.State.TEST

    if request.method == "POST":
        if target == Experiment.State.ACTIVE and problems:
            messages.error(
                request, "Cannot activate yet — " + " ".join(problems)
            )
            return redirect(request.path)
        if action == "activate" and from_test:
            _activate_from_test(request, experiment)
            return redirect("studio:study_overview", slug=slug)
        old_state = experiment.state
        experiment.state = target
        try:
            # The model gates (walkable for →TEST, readiness for →ACTIVE,
            # allowed transitions) stay the enforcement of record.
            experiment.full_clean()
        except ValidationError as exc:
            experiment.state = old_state
            for msgs in exc.message_dict.values():
                for msg in msgs:
                    messages.error(request, msg)
            return redirect(request.path)
        experiment.save(update_fields=["state"])
        _audit(request, experiment, "state", from_state=old_state, to_state=target)
        messages.success(
            request,
            f"'{experiment.name}' is now {experiment.get_state_display().lower()}.",
        )
        return redirect("studio:study_overview", slug=slug)

    return render(
        request,
        "studio/state_confirm.html",
        {
            **_study_nav_context(request, experiment),
            "action": action,
            "target_state": target,
            "target_display": Experiment.State(target).label,
            "readiness_problems": problems,
            "counts": experiment_counts(experiment),
            "offer_test_data_choice": action == "activate" and from_test,
        },
    )


# --- conditions & stimuli authoring ------------------------------------------


def _editable_study_or_404(request, slug):
    experiment = _experiment_or_404(request, slug)
    if not can_edit(request.user, experiment):
        raise PermissionDenied
    return experiment


def _require_draft(request, experiment) -> bool:
    """Structural edits are draft-only (same lock as the question builder);
    returns False (with a message) when the study has left draft."""
    if experiment.state == Experiment.State.DRAFT:
        return True
    messages.error(
        request,
        "Conditions and stimuli can only be edited while the study is a draft.",
    )
    return False


@login_required
def stimuli_overview(request, slug):
    experiment = _editable_study_or_404(request, slug)
    conditions = [
        {"condition": cond, "stimuli": list(cond.stimuli.order_by("sort_order", "id"))}
        for cond in experiment.conditions.order_by("name")
    ]
    return render(
        request,
        "studio/stimuli_overview.html",
        {
            **_study_nav_context(request, experiment),
            "conditions": conditions,
            "is_draft": experiment.state == Experiment.State.DRAFT,
            "readiness_problems": readiness_problems(experiment),
        },
    )


@login_required
def condition_edit(request, slug, pk=None):
    experiment = _editable_study_or_404(request, slug)
    instance = (
        get_object_or_404(Condition, pk=pk, experiment=experiment)
        if pk
        else Condition(experiment=experiment)
    )
    if request.method == "POST":
        if not _require_draft(request, experiment):
            return redirect("studio:stimuli", slug=slug)
        form = ConditionForm(request.POST, instance=instance)
        if form.is_valid():
            form.save()
            _audit(request, experiment, "condition",
                   name=form.instance.name, created=pk is None)
            messages.success(
                request,
                f"Condition '{form.instance.name}' "
                f"{'created' if pk is None else 'updated'}.",
            )
            return redirect("studio:stimuli", slug=slug)
    else:
        form = ConditionForm(instance=instance)
    return render(
        request,
        "studio/condition_form.html",
        {
            **_study_nav_context(request, experiment),
            "form": form,
            "is_new": pk is None,
            "is_draft": experiment.state == Experiment.State.DRAFT,
        },
    )


@login_required
def condition_delete(request, slug, pk):
    experiment = _editable_study_or_404(request, slug)
    if request.method != "POST":
        return redirect("studio:stimuli", slug=slug)
    if not _require_draft(request, experiment):
        return redirect("studio:stimuli", slug=slug)
    condition = get_object_or_404(Condition, pk=pk, experiment=experiment)
    name = condition.name
    condition.delete()
    _audit(request, experiment, "condition", name=name, deleted=True)
    messages.success(request, f"Condition '{name}' and its stimuli were deleted.")
    return redirect("studio:stimuli", slug=slug)


@login_required
def stimulus_edit(request, slug, pk=None):
    experiment = _editable_study_or_404(request, slug)
    instance = (
        get_object_or_404(Stimulus, pk=pk, condition__experiment=experiment)
        if pk
        else None
    )
    if request.method == "POST":
        if not _require_draft(request, experiment):
            return redirect("studio:stimuli", slug=slug)
        form = StimulusForm(
            request.POST, request.FILES, instance=instance, experiment=experiment
        )
        if form.is_valid():
            form.save()
            _audit(request, experiment, "stimulus",
                   title=form.instance.title or str(form.instance.pk),
                   kind=form.instance.kind, created=pk is None)
            messages.success(
                request,
                f"Stimulus {'created' if pk is None else 'updated'}.",
            )
            return redirect("studio:stimuli", slug=slug)
    else:
        initial = {}
        if pk is None:
            if request.GET.get("condition"):
                initial["condition"] = request.GET["condition"]
            if request.GET.get("kind"):
                initial["kind"] = request.GET["kind"]
        form = StimulusForm(instance=instance, experiment=experiment, initial=initial)
    return render(
        request,
        "studio/stimulus_form.html",
        {
            **_study_nav_context(request, experiment),
            "form": form,
            "is_new": pk is None,
            "is_draft": experiment.state == Experiment.State.DRAFT,
        },
    )


@login_required
def stimulus_delete(request, slug, pk):
    experiment = _editable_study_or_404(request, slug)
    if request.method != "POST":
        return redirect("studio:stimuli", slug=slug)
    if not _require_draft(request, experiment):
        return redirect("studio:stimuli", slug=slug)
    stimulus = get_object_or_404(Stimulus, pk=pk, condition__experiment=experiment)
    title = stimulus.title or str(stimulus.pk)
    stimulus.delete()
    _audit(request, experiment, "stimulus", title=title, deleted=True)
    messages.success(request, f"Stimulus '{title}' was deleted.")
    return redirect("studio:stimuli", slug=slug)


# --- drag-&-drop question builder ------------------------------------------


def _builtin_default_config():
    # Built-in seed configs live on the components now (question_types).
    from experiments.question_types import builtin_default_config

    return builtin_default_config()


def _palette() -> list[dict]:
    """Question types offered in the builder: built-ins + registered plugins."""
    _builtin_defaults = _builtin_default_config()
    palette = [
        {
            "type": value,
            "label": label,
            "plugin": False,
            "default_config": _builtin_defaults.get(value, {}),
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
            **_study_nav_context(request, experiment),
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

    _audit(request, experiment, "questions", count=len(prepared))
    # ids are returned in posted order so the builder can adopt them on the
    # new cards (a second save then updates instead of re-creating).
    return JsonResponse(
        {"ok": True, "count": len(prepared), "ids": [q.pk for q in prepared]}
    )


def _float(value, default):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _pilot_power_rows(experiment, alpha, power):
    """Observed effect size + implied sample size from pilot data, per rating /
    numeric / likert per-stimulus question (its two best-attested conditions)."""
    rows = []
    numeric_types = (
        Question.Type.RATING, Question.Type.NUMERIC, Question.Type.LIKERT,
    )
    qs = experiment.questions.filter(
        section=Question.Section.STIMULUS, type__in=numeric_types
    )
    for q in qs:
        numeric = {}
        for cond, values in _grouped_answers(experiment, q).items():
            nums = []
            for v in values:
                try:
                    nums.append(float(v))
                except (TypeError, ValueError):
                    continue
            if len(nums) >= 2:
                numeric[cond] = nums
        if len(numeric) < 2:
            continue
        top = sorted(numeric.values(), key=len, reverse=True)[:2]
        d = cohens_d(top[0], top[1])
        if d is None or d == 0:
            continue
        rows.append({
            "prompt": q.prompt,
            "effect_size": abs(d),
            "observed_n": min(len(top[0]), len(top[1])),
            "required_n": required_n_per_group(abs(d), alpha, power),
            "achieved_power": achieved_power(abs(d), min(len(top[0]), len(top[1])), alpha),
        })
    return rows


@login_required
def power_analysis(request, slug):
    experiment = _experiment_or_404(request, slug)
    d = _float(request.GET.get("d"), 0.5)
    alpha = _float(request.GET.get("alpha"), 0.05)
    target = _float(request.GET.get("power"), 0.8)
    manual = {
        "d": d, "alpha": alpha, "power": target,
        "required_n": required_n_per_group(d, alpha, target),
    }
    return render(
        request,
        "studio/power.html",
        {
            **_study_nav_context(request, experiment),
            "manual": manual,
            "pilot": _pilot_power_rows(experiment, alpha, target),
        },
    )


@login_required
def study_webhooks(request, slug):
    """Manage outbound webhooks for a study (owner/manager only)."""
    experiment = _experiment_or_404(request, slug)
    if not can_manage(request.user, experiment):
        raise PermissionDenied
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "add":
            url = (request.POST.get("url") or "").strip()
            event = request.POST.get("event") or Webhook.Event.SESSION_COMPLETED
            if url and event in dict(Webhook.Event.choices):
                Webhook.objects.create(experiment=experiment, url=url, event=event)
                _audit(request, experiment, "webhook")
                messages.success(request, "Webhook added.")
            else:
                messages.error(request, "Enter a valid URL and event.")
        elif action == "delete":
            Webhook.objects.filter(
                experiment=experiment, pk=request.POST.get("webhook_id")
            ).delete()
            messages.success(request, "Webhook removed.")
        return redirect("studio:study_webhooks", slug=slug)
    return render(
        request,
        "studio/study_webhooks.html",
        {
            **_study_nav_context(request, experiment),
            "webhooks": list(experiment.webhooks.all()),
            "events": Webhook.Event.choices,
        },
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
                    f"You've been invited as {invitation.role} on the PANEL "
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


def _include_pii(request) -> bool:
    return request.GET.get("include_pii") in ("1", "true", "on")


def _audit_export(request, experiment, target):
    _audit(request, experiment, target, action=AuditEvent.Action.EXPORT,
           include_pii=_include_pii(request))


@login_required
def answers_csv(request, slug):
    experiment = _experiment_or_404(request, slug)
    _audit_export(request, experiment, "answers.csv")
    return answers_csv_response(
        experiment,
        exclude_flagged=_exclude_flagged(request),
        include_pii=_include_pii(request),
    )


@login_required
def demographics_csv(request, slug):
    experiment = _experiment_or_404(request, slug)
    _audit_export(request, experiment, "demographics.csv")
    return demographics_csv_response(
        experiment,
        exclude_flagged=_exclude_flagged(request),
        include_pii=_include_pii(request),
    )


@login_required
def completion_codes_csv(request, slug):
    experiment = _experiment_or_404(request, slug)
    _audit_export(request, experiment, "completion-codes.csv")
    return completion_codes_csv_response(experiment)


@login_required
def events_csv(request, slug):
    experiment = _experiment_or_404(request, slug)
    _audit_export(request, experiment, "events.csv")
    return events_csv_response(experiment)


@login_required
def pairwise_csv(request, slug):
    experiment = _experiment_or_404(request, slug)
    _audit_export(request, experiment, "pairwise-answers.csv")
    return pairwise_answers_csv_response(experiment)


@login_required
def export_zip(request, slug):
    experiment = _experiment_or_404(request, slug)
    _audit_export(request, experiment, f"{experiment.slug}.zip")
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
