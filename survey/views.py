"""Participant-facing views for the survey flow.

The flow is a fixed state machine (see :mod:`survey.flow`):

    consent → (audio_check) → instructions → stimuli → demographics → thanks

Each survey is reached directly via ``/s/<slug>/``; there is no public
landing page. The stimulus and demographic phases are paginated
PsyToolkit-style: a page holds one or more questions joined by their
author-controlled ``page_break_before`` flag, and every Next button POSTs
only the answers on the current page.
"""
from __future__ import annotations

import json
import random
from typing import Any

from django.contrib import messages
from django.db import transaction
from django.db.models import F
from django.http import (
    Http404,
    HttpResponseBadRequest,
    HttpResponseRedirect,
    JsonResponse,
)
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from experiments.assignment import (
    UnknownStrategyError,
    get_pairwise_strategy,
    get_strategy,
)
from experiments.models import Experiment, Question, Stimulus

from .flow import (
    paginate_questions,
    pairwise_progress_percent,
    progress_percent,
    required_step_url,
)
from .metadata import extract_metadata
from .models import PairAssignment, ParticipantSession, Response, StimulusAssignment


# --- helpers ---------------------------------------------------------------


def _session_key(slug: str) -> str:
    return f"webeval:session:{slug}"


def _load_session(
    request, slug: str
) -> tuple[Experiment, ParticipantSession | None]:
    experiment = get_object_or_404(Experiment, slug=slug)
    key = _session_key(slug)
    session_id = request.session.get(key)
    session: ParticipantSession | None = None
    if session_id:
        session = ParticipantSession.objects.filter(pk=session_id).first()
        if session and session.experiment_id != experiment.pk:
            session = None
    return experiment, session


def _redirect_to_step(session: ParticipantSession) -> HttpResponseRedirect:
    return HttpResponseRedirect(required_step_url(session))


def _expect_step(session: ParticipantSession, step: str) -> HttpResponseRedirect | None:
    if session.last_step != step:
        return _redirect_to_step(session)
    return None


def _split_consent_text(text: str) -> tuple[str, str]:
    """Return (first paragraph, remaining text). Splits on the first blank line."""
    if not text:
        return "", ""
    normalised = text.replace("\r\n", "\n").replace("\r", "\n")
    parts = normalised.split("\n\n", 1)
    first = parts[0].strip()
    rest = parts[1].strip() if len(parts) > 1 else ""
    return first, rest


def _unavailable(request, experiment: Experiment):
    return render(
        request,
        "survey/unavailable.html",
        {"experiment": experiment, "brand": experiment.name},
        status=200,
    )


def _serialise_answer(question: Question, raw_value: Any) -> Any:
    """Coerce a POSTed string into a typed answer according to question type."""
    if question.type == Question.Type.RATING:
        return int(raw_value)
    if question.type == Question.Type.LIKERT:
        return int(raw_value)
    if question.type == Question.Type.CHOICE:
        if isinstance(raw_value, list):
            return raw_value
        return str(raw_value)
    return str(raw_value)


def _ordered_section_questions(
    experiment: Experiment, section: str
) -> list[Question]:
    return list(
        experiment.questions.filter(section=section).order_by("sort_order", "pk")
    )


def _stimulus_questions(experiment: Experiment, session: ParticipantSession) -> list[Question]:
    """Return stimulus-section questions in the session-randomised order."""
    key = f"webeval:q_order:{session.id}:stimulus"
    cached = getattr(session, "_cached_question_order", None)
    if cached is None:
        questions = _ordered_section_questions(experiment, Question.Section.STIMULUS)
        ids_to_q = {q.pk: q for q in questions}
        ordered_ids = list(ids_to_q.keys())
        rng = random.Random(str(session.id))
        rng.shuffle(ordered_ids)
        cached = [ids_to_q[i] for i in ordered_ids]
        session._cached_question_order = cached  # type: ignore[attr-defined]
    return cached  # type: ignore[return-value]


def _experiment_has_audio(experiment: Experiment) -> bool:
    return Stimulus.objects.filter(
        condition__experiment=experiment, kind=Stimulus.Kind.AUDIO
    ).exists()


def _audio_check_active(experiment: Experiment) -> bool:
    return bool(experiment.require_audio_check) and _experiment_has_audio(experiment)


def _progress(
    experiment: Experiment, session: ParticipantSession
) -> int:
    dem_pages = len(
        paginate_questions(_ordered_section_questions(experiment, Question.Section.DEMOGRAPHIC))
    )
    audio_check = _audio_check_active(experiment)
    if experiment.mode == Experiment.Mode.PAIRWISE:
        return pairwise_progress_percent(
            session,
            pairs_total=session.pair_assignments.count(),
            demographic_pages=dem_pages,
            audio_check=audio_check,
        )
    stim_pages = len(
        paginate_questions(_stimulus_questions(experiment, session))
    )
    return progress_percent(
        session,
        stimulus_pages_per_assignment=stim_pages,
        demographic_pages=dem_pages,
        assignments_total=session.assignments.count(),
        audio_check=audio_check,
    )


def _base_context(experiment: Experiment, session: ParticipantSession | None) -> dict[str, Any]:
    ctx: dict[str, Any] = {
        "experiment": experiment,
        "brand": experiment.name,
    }
    if session is not None:
        ctx["session"] = session
        ctx["progress_percent"] = _progress(experiment, session)
    else:
        ctx["progress_percent"] = 0
    return ctx


# --- consent ---------------------------------------------------------------


@require_http_methods(["GET", "POST"])
def consent(request, slug: str):
    experiment, session = _load_session(request, slug)
    if experiment.state != Experiment.State.ACTIVE:
        return _unavailable(request, experiment)

    consent_first, consent_rest = _split_consent_text(experiment.consent_text)

    if request.method == "POST":
        if not request.POST.get("agree"):
            messages.error(
                request,
                "You must tick the consent checkbox to take part in the study.",
            )
            ctx = _base_context(experiment, session)
            ctx["error"] = True
            ctx["consent_first"] = consent_first
            ctx["consent_rest"] = consent_rest
            return render(request, "survey/consent.html", ctx)
        if session is None:
            session = _create_session(request, experiment)
        session.consented_at = timezone.now()
        needs_audio_check = _audio_check_active(experiment)
        session.last_step = (
            ParticipantSession.Step.AUDIO_CHECK
            if needs_audio_check
            else ParticipantSession.Step.INSTRUCTIONS
        )
        session.save(update_fields=["consented_at", "last_step"])
        if needs_audio_check:
            return redirect("survey:audio_check", slug=slug)
        return redirect("survey:instructions", slug=slug)

    Experiment.objects.filter(pk=experiment.pk).update(
        consent_page_views=F("consent_page_views") + 1
    )
    ctx = _base_context(experiment, session)
    ctx["consent_first"] = consent_first
    ctx["consent_rest"] = consent_rest
    return render(request, "survey/consent.html", ctx)


def _create_session(request, experiment: Experiment) -> ParticipantSession:
    meta = extract_metadata(request)
    session = ParticipantSession.objects.create(
        experiment=experiment,
        device_type=meta.device_type,
        browser_family=meta.browser_family,
        country_code=meta.country_code,
    )
    request.session[_session_key(experiment.slug)] = str(session.id)
    return session


# --- instructions ----------------------------------------------------------


@require_http_methods(["GET", "POST"])
def instructions(request, slug: str):
    experiment, session = _load_session(request, slug)
    if experiment.state != Experiment.State.ACTIVE:
        return _unavailable(request, experiment)
    if session is None:
        return redirect("survey:consent", slug=slug)
    bounce = _expect_step(session, ParticipantSession.Step.INSTRUCTIONS)
    if bounce:
        return bounce

    if request.method == "POST":
        is_pairwise = experiment.mode == Experiment.Mode.PAIRWISE
        next_step = ParticipantSession.Step.STIMULI
        with transaction.atomic():
            if is_pairwise:
                _build_pair_assignments(session)
                session.last_step = next_step
                session.current_pair_index = 0
                session.save(update_fields=["last_step", "current_pair_index"])
            else:
                _build_assignments(session)
                session.last_step = next_step
                session.current_assignment_index = 0
                session.current_page_index = 0
                session.save(
                    update_fields=[
                        "last_step",
                        "current_assignment_index",
                        "current_page_index",
                    ]
                )
        if is_pairwise:
            return redirect("survey:pairwise_play", slug=slug)
        return redirect("survey:play", slug=slug)

    return render(
        request,
        "survey/instructions.html",
        _base_context(experiment, session),
    )


def _build_assignments(session: ParticipantSession) -> None:
    if session.assignments.exists():
        return
    try:
        strategy = get_strategy(session.experiment.assignment_strategy)
    except UnknownStrategyError:
        strategy = get_strategy("balanced_random")
    counts = _fetch_counts(session.experiment)
    selected: list[Stimulus] = strategy.select(
        experiment=session.experiment,
        n=session.experiment.stimuli_per_participant,
        counts=counts,
        rng=random.Random(str(session.id)),
    )
    for order, stim in enumerate(selected):
        StimulusAssignment.objects.create(
            session=session,
            stimulus=stim,
            sort_order=order,
        )


def _fetch_counts(experiment: Experiment) -> dict[int, int]:
    from django.db.models import Count

    rows = (
        StimulusAssignment.objects.filter(stimulus__condition__experiment=experiment)
        .values("stimulus_id")
        .annotate(n=Count("pk"))
    )
    return {row["stimulus_id"]: row["n"] for row in rows}


# --- audio check -----------------------------------------------------------


@require_http_methods(["GET", "POST"])
def audio_check(request, slug: str):
    experiment, session = _load_session(request, slug)
    if experiment.state != Experiment.State.ACTIVE:
        return _unavailable(request, experiment)
    if session is None:
        return redirect("survey:consent", slug=slug)
    bounce = _expect_step(session, ParticipantSession.Step.AUDIO_CHECK)
    if bounce:
        return bounce

    if request.method == "POST":
        can_hear = request.POST.get("can_hear") == "yes"
        if not can_hear:
            messages.error(
                request,
                "Please confirm you can hear the audio clearly at a comfortable volume.",
            )
            ctx = _base_context(experiment, session)
            ctx["can_hear"] = can_hear
            return render(request, "survey/audio_check.html", ctx, status=400)

        session.last_step = ParticipantSession.Step.INSTRUCTIONS
        session.save(update_fields=["last_step"])
        return redirect("survey:instructions", slug=slug)

    return render(
        request,
        "survey/audio_check.html",
        _base_context(experiment, session),
    )


# --- stimulus play ---------------------------------------------------------


@require_http_methods(["GET", "POST"])
def play(request, slug: str):
    experiment, session = _load_session(request, slug)
    if experiment.state != Experiment.State.ACTIVE:
        return _unavailable(request, experiment)
    if session is None:
        return redirect("survey:consent", slug=slug)
    bounce = _expect_step(session, ParticipantSession.Step.STIMULI)
    if bounce:
        return bounce

    assignments = list(
        session.assignments.select_related("stimulus", "stimulus__condition").order_by(
            "sort_order"
        )
    )
    if not assignments:
        # No stimuli configured — skip straight to demographics.
        session.last_step = ParticipantSession.Step.DEMOGRAPHICS
        session.save(update_fields=["last_step"])
        return redirect("survey:demographics", slug=slug)

    # Clamp a runaway cursor (should not happen, but defend against it).
    if session.current_assignment_index >= len(assignments):
        session.last_step = ParticipantSession.Step.DEMOGRAPHICS
        session.save(update_fields=["last_step"])
        return redirect("survey:demographics", slug=slug)

    assignment = assignments[session.current_assignment_index]
    questions = _stimulus_questions(experiment, session)
    pages = paginate_questions(questions)
    if not pages:
        # No stimulus-section questions configured; jump forward.
        session.last_step = ParticipantSession.Step.DEMOGRAPHICS
        session.save(update_fields=["last_step"])
        return redirect("survey:demographics", slug=slug)

    if session.current_page_index >= len(pages):
        session.current_page_index = 0
        session.current_assignment_index += 1
        session.save(update_fields=["current_page_index", "current_assignment_index"])
        return redirect("survey:play", slug=slug)

    page_questions = pages[session.current_page_index]
    is_last_assignment = session.current_assignment_index == len(assignments) - 1
    is_last_page = is_last_assignment and session.current_page_index == len(pages) - 1

    if request.method == "POST":
        return _save_page_answers(
            request,
            session,
            assignment,
            page_questions,
            pages,
            assignments,
            slug,
        )

    ctx = _base_context(experiment, session)
    ctx.update(
        {
            "assignment": assignment,
            "stimulus": assignment.stimulus,
            "page_questions": page_questions,
            "is_last_page": is_last_page,
            "has_more_after_stimuli": _ordered_section_questions(
                experiment, Question.Section.DEMOGRAPHIC
            ),
            "show_prompt": any(q.show_prompt for q in page_questions),
        }
    )
    return render(request, "survey/play.html", ctx)


def _save_page_answers(
    request,
    session: ParticipantSession,
    assignment: StimulusAssignment,
    page_questions: list[Question],
    pages: list[list[Question]],
    assignments: list[StimulusAssignment],
    slug: str,
):
    errors, responses = _collect_answers(
        request, session, assignment.stimulus, page_questions
    )
    if errors:
        for err in errors:
            messages.error(request, err)
        experiment = session.experiment
        _annotate_submitted(request, page_questions)
        ctx = _base_context(experiment, session)
        ctx.update(
            {
                "assignment": assignment,
                "stimulus": assignment.stimulus,
                "page_questions": page_questions,
                "is_last_page": (
                    session.current_assignment_index == len(assignments) - 1
                    and session.current_page_index == len(pages) - 1
                ),
                "show_prompt": any(q.show_prompt for q in page_questions),
            }
        )
        return render(request, "survey/play.html", ctx, status=400)

    with transaction.atomic():
        Response.objects.bulk_create(responses)
        session.current_page_index += 1
        if session.current_page_index >= len(pages):
            session.current_page_index = 0
            session.current_assignment_index += 1
            if session.current_assignment_index >= len(assignments):
                session.last_step = ParticipantSession.Step.DEMOGRAPHICS
                session.demographic_page_index = 0
                session.save(
                    update_fields=[
                        "current_page_index",
                        "current_assignment_index",
                        "last_step",
                        "demographic_page_index",
                    ]
                )
                return redirect("survey:demographics", slug=slug)
        session.save(
            update_fields=["current_page_index", "current_assignment_index"]
        )
    return redirect("survey:play", slug=slug)


def _annotate_submitted(request, questions: list[Question]) -> None:
    for q in questions:
        key = f"q_{q.pk}"
        if q.type == Question.Type.CHOICE and q.config.get("multi"):
            q.submitted_values = request.POST.getlist(key)
        else:
            q.submitted_value = request.POST.get(key, "")


def _collect_answers(
    request,
    session: ParticipantSession,
    stimulus: Stimulus | None,
    questions: list[Question],
) -> tuple[list[str], list[Response]]:
    errors: list[str] = []
    responses: list[Response] = []
    for q in questions:
        if q.type == Question.Type.CHOICE and q.config.get("multi"):
            raw_list = request.POST.getlist(f"q_{q.pk}")
            if not raw_list:
                if q.required:
                    errors.append(f"'{q.prompt}' is required.")
                continue
            try:
                value = _serialise_answer(q, raw_list)
            except (TypeError, ValueError):
                errors.append(f"'{q.prompt}' has an invalid value.")
                continue
        else:
            raw = request.POST.get(f"q_{q.pk}")
            if raw is None or raw == "":
                if q.required:
                    errors.append(f"'{q.prompt}' is required.")
                continue
            try:
                value = _serialise_answer(q, raw)
            except (TypeError, ValueError):
                errors.append(f"'{q.prompt}' has an invalid value.")
                continue
        responses.append(
            Response(
                session=session,
                stimulus=stimulus,
                question=q,
                answer_value=json.dumps(value, ensure_ascii=False),
            )
        )
    return errors, responses


# --- pairwise assignments --------------------------------------------------


def _build_pair_assignments(session: ParticipantSession) -> None:
    if session.pair_assignments.exists():
        return
    try:
        strategy = get_pairwise_strategy(session.experiment.assignment_strategy)
    except UnknownStrategyError:
        strategy = get_pairwise_strategy("pairwise_balanced")
    pair_counts = _fetch_pair_counts(session.experiment)
    specs = strategy.select_pairs(
        experiment=session.experiment,
        n=session.experiment.stimuli_per_participant,
        pair_counts=pair_counts,
        rng=random.Random(str(session.id)),
    )
    for order, spec in enumerate(specs):
        PairAssignment.objects.create(
            session=session,
            stimulus_a_id=spec.stimulus_a_id,
            stimulus_b_id=spec.stimulus_b_id,
            prompt_group=spec.prompt_group,
            position_a=spec.position_a,
            sort_order=order,
        )


def _fetch_pair_counts(experiment: Experiment) -> dict[tuple[int, int], int]:
    from django.db.models import Count

    rows = (
        PairAssignment.objects.filter(session__experiment=experiment)
        .values("stimulus_a__condition_id", "stimulus_b__condition_id")
        .annotate(n=Count("pk"))
    )
    counts: dict[tuple[int, int], int] = {}
    for row in rows:
        a = row["stimulus_a__condition_id"]
        b = row["stimulus_b__condition_id"]
        key = (min(a, b), max(a, b))
        counts[key] = counts.get(key, 0) + row["n"]
    return counts


# --- pairwise play ---------------------------------------------------------


@require_http_methods(["GET", "POST"])
def pairwise_play(request, slug: str):
    experiment, session = _load_session(request, slug)
    if experiment.state != Experiment.State.ACTIVE:
        return _unavailable(request, experiment)
    if session is None:
        return redirect("survey:consent", slug=slug)
    bounce = _expect_step(session, ParticipantSession.Step.STIMULI)
    if bounce:
        return bounce

    pairs = list(
        session.pair_assignments.select_related(
            "stimulus_a", "stimulus_a__condition",
            "stimulus_b", "stimulus_b__condition",
        ).order_by("sort_order")
    )
    if not pairs:
        session.last_step = ParticipantSession.Step.DEMOGRAPHICS
        session.save(update_fields=["last_step"])
        return redirect("survey:demographics", slug=slug)

    if session.current_pair_index >= len(pairs):
        session.last_step = ParticipantSession.Step.DEMOGRAPHICS
        session.demographic_page_index = 0
        session.save(update_fields=["last_step", "demographic_page_index"])
        return redirect("survey:demographics", slug=slug)

    pair = pairs[session.current_pair_index]
    questions = _stimulus_questions(experiment, session)
    is_last_pair = session.current_pair_index == len(pairs) - 1
    has_demographics = bool(
        _ordered_section_questions(experiment, Question.Section.DEMOGRAPHIC)
    )

    if request.method == "POST":
        errors, responses = _collect_pairwise_answers(
            request, session, pair, questions
        )
        if errors:
            for err in errors:
                messages.error(request, err)
            _annotate_submitted(request, questions)
            ctx = _base_context(experiment, session)
            ctx.update({
                "pair": pair,
                "questions": questions,
                "is_last_pair": is_last_pair,
                "has_demographics": has_demographics,
                "pair_number": session.current_pair_index + 1,
                "pairs_total": len(pairs),
                "show_prompt": any(q.show_prompt for q in questions),
            })
            return render(request, "survey/pairwise_play.html", ctx, status=400)

        with transaction.atomic():
            Response.objects.bulk_create(responses)
            session.current_pair_index += 1
            if session.current_pair_index >= len(pairs):
                session.last_step = ParticipantSession.Step.DEMOGRAPHICS
                session.demographic_page_index = 0
                session.save(
                    update_fields=[
                        "current_pair_index",
                        "last_step",
                        "demographic_page_index",
                    ]
                )
                return redirect("survey:demographics", slug=slug)
            session.save(update_fields=["current_pair_index"])
        return redirect("survey:pairwise_play", slug=slug)

    ctx = _base_context(experiment, session)
    ctx.update({
        "pair": pair,
        "questions": questions,
        "is_last_pair": is_last_pair,
        "has_demographics": has_demographics,
        "pair_number": session.current_pair_index + 1,
        "pairs_total": len(pairs),
        "show_prompt": any(q.show_prompt for q in questions),
    })
    return render(request, "survey/pairwise_play.html", ctx)


def _collect_pairwise_answers(
    request,
    session: ParticipantSession,
    pair: PairAssignment,
    questions: list[Question],
) -> tuple[list[str], list[Response]]:
    errors: list[str] = []
    responses: list[Response] = []
    for q in questions:
        if q.type == Question.Type.CHOICE and q.config.get("multi"):
            raw_list = request.POST.getlist(f"q_{q.pk}")
            if not raw_list:
                if q.required:
                    errors.append(f"'{q.prompt}' is required.")
                continue
            try:
                value = _serialise_answer(q, raw_list)
            except (TypeError, ValueError):
                errors.append(f"'{q.prompt}' has an invalid value.")
                continue
        else:
            raw = request.POST.get(f"q_{q.pk}")
            if raw is None or raw == "":
                if q.required:
                    errors.append(f"'{q.prompt}' is required.")
                continue
            try:
                value = _serialise_answer(q, raw)
            except (TypeError, ValueError):
                errors.append(f"'{q.prompt}' has an invalid value.")
                continue
        responses.append(
            Response(
                session=session,
                stimulus=None,
                pair_assignment=pair,
                question=q,
                answer_value=json.dumps(value, ensure_ascii=False),
            )
        )
    return errors, responses


# --- listen duration endpoint ----------------------------------------------


@require_POST
def record_listen(request, slug: str, assignment_id: int):
    experiment, session = _load_session(request, slug)
    if session is None:
        return HttpResponseBadRequest("no session")
    assignment = get_object_or_404(
        StimulusAssignment, pk=assignment_id, session=session
    )
    try:
        payload = json.loads(request.body or b"{}")
        duration_ms = int(payload.get("duration_ms", 0))
    except (ValueError, TypeError):
        return HttpResponseBadRequest("invalid payload")
    if duration_ms < 0:
        duration_ms = 0
    assignment.listen_duration_ms = max(assignment.listen_duration_ms, duration_ms)
    if assignment.started_listening_at is None:
        assignment.started_listening_at = timezone.now()
    assignment.save(update_fields=["listen_duration_ms", "started_listening_at"])
    return JsonResponse({"ok": True, "listen_duration_ms": assignment.listen_duration_ms})


@require_POST
def record_listen_pair(request, slug: str, pair_id: int):
    experiment, session = _load_session(request, slug)
    if session is None:
        return HttpResponseBadRequest("no session")
    pair = get_object_or_404(PairAssignment, pk=pair_id, session=session)
    try:
        payload = json.loads(request.body or b"{}")
        duration_ms = int(payload.get("duration_ms", 0))
        side = str(payload.get("side", ""))
    except (ValueError, TypeError):
        return HttpResponseBadRequest("invalid payload")
    if duration_ms < 0:
        duration_ms = 0
    if side == "a":
        pair.listen_duration_a_ms = max(pair.listen_duration_a_ms, duration_ms)
        pair.save(update_fields=["listen_duration_a_ms"])
    elif side == "b":
        pair.listen_duration_b_ms = max(pair.listen_duration_b_ms, duration_ms)
        pair.save(update_fields=["listen_duration_b_ms"])
    else:
        return HttpResponseBadRequest("side must be 'a' or 'b'")
    return JsonResponse({"ok": True})


# --- demographics ----------------------------------------------------------


@require_http_methods(["GET", "POST"])
def demographics(request, slug: str):
    experiment, session = _load_session(request, slug)
    if experiment.state != Experiment.State.ACTIVE:
        return _unavailable(request, experiment)
    if session is None:
        return redirect("survey:consent", slug=slug)
    bounce = _expect_step(session, ParticipantSession.Step.DEMOGRAPHICS)
    if bounce:
        return bounce

    questions = _ordered_section_questions(experiment, Question.Section.DEMOGRAPHIC)
    pages = paginate_questions(questions)
    if not pages:
        return _finish_session(request, session, slug)

    if session.demographic_page_index >= len(pages):
        return _finish_session(request, session, slug)

    page_questions = pages[session.demographic_page_index]
    is_last_page = session.demographic_page_index == len(pages) - 1

    if request.method == "POST":
        errors, responses = _collect_answers(request, session, None, page_questions)
        if errors:
            for err in errors:
                messages.error(request, err)
            _annotate_submitted(request, page_questions)
            ctx = _base_context(experiment, session)
            ctx.update({"page_questions": page_questions, "is_last_page": is_last_page})
            return render(request, "survey/demographics.html", ctx, status=400)
        with transaction.atomic():
            Response.objects.bulk_create(responses)
            session.demographic_page_index += 1
            if session.demographic_page_index >= len(pages):
                return _finish_session(request, session, slug)
            session.save(update_fields=["demographic_page_index"])
        return redirect("survey:demographics", slug=slug)

    ctx = _base_context(experiment, session)
    ctx.update({"page_questions": page_questions, "is_last_page": is_last_page})
    return render(request, "survey/demographics.html", ctx)


def _finish_session(request, session: ParticipantSession, slug: str):
    session.submitted_at = timezone.now()
    session.last_step = ParticipantSession.Step.DONE
    session.save(update_fields=["submitted_at", "last_step", "demographic_page_index"])
    request.session.pop(_session_key(slug), None)
    return redirect("survey:thanks", slug=slug)


# --- thanks ---------------------------------------------------------------


def thanks(request, slug: str):
    experiment = get_object_or_404(Experiment, slug=slug)
    return render(
        request,
        "survey/thanks.html",
        {"experiment": experiment, "brand": experiment.name, "progress_percent": 100},
    )
