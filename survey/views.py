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
import logging
import random
import secrets
import uuid
from datetime import timedelta
from dataclasses import dataclass
from typing import Any, Callable

from django.contrib import messages
from django.core.mail import send_mail
from django.db import transaction
from django.db.models import F
from django.http import (
    HttpResponseBadRequest,
    HttpResponseRedirect,
    JsonResponse,
)
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods, require_POST

from experiments.assignment import (
    UnknownStrategyError,
    get_pairwise_strategy,
    get_strategy,
)
from experiments.branching import (
    evaluate_condition,
    is_visible,
    referenced_question_ids,
)
from experiments.models import (
    Experiment,
    ParticipantInvite,
    Prompt,
    Question,
    Stimulus,
)

from .flagging import compute_flags
from .flow import (
    paginate_questions,
    pairwise_progress_percent,
    progress_percent,
    required_step_url,
)
from .metadata import extract_metadata
from .models import (
    PairAssignment,
    ParticipantSession,
    Response,
    StimulusAssignment,
    SurveyEvent,
)

logger = logging.getLogger(__name__)


# --- helpers ---------------------------------------------------------------


# States in which the survey is reachable by participants. ACTIVE collects
# real data; TEST behaves identically but is a pre-launch rehearsal — a
# banner is rendered and the data can be wiped on TEST→ACTIVE promotion.
RUNNABLE_STATES: frozenset[str] = frozenset(
    {Experiment.State.ACTIVE, Experiment.State.TEST}
)


PARTICIPANT_COOKIE = "webeval_pid"
PARTICIPANT_COOKIE_MAX_AGE = 60 * 60 * 24 * 365  # 1 year


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


def _read_one(request, question: Question) -> tuple[bool, Any, str | None]:
    """Read one question's answer from POST.

    Returns ``(answered, value, error)``: whether any input was given, the
    serialisable typed value, and a human-readable problem (or None). Every
    type — built-in or plugin — parses through its component
    (``experiments.question_types.resolve_component``), so the standard and
    pairwise flows behave identically. An unregistered (orphaned) type reads as
    unanswered; the widget shows a "not installed" notice and readiness blocks
    activation on such types.
    """
    from experiments.question_types import resolve_component

    component = resolve_component(question.type)
    if component is not None:
        return component.read_answer(request.POST, question)
    return False, None, None


def _answers_for_stimulus(session, stimulus) -> dict[int, Any]:
    """Map question_id → stored answer for one stimulus in this session."""
    return {
        r.question_id: r.get_answer()
        for r in Response.objects.filter(session=session, stimulus=stimulus)
    }


def _answers_for_section(session, section) -> dict[int, Any]:
    """Map question_id → stored answer for one session-level section.

    Screening and demographic answers both store Responses with no stimulus and
    no pair_assignment, so they are disambiguated by the question's section.
    """
    return {
        r.question_id: r.get_answer()
        for r in Response.objects.filter(
            session=session,
            stimulus__isnull=True,
            pair_assignment__isnull=True,
            question__section=section,
        )
    }


def _visible_with_submitted(request, page_questions, stored):
    """Page questions whose ``visible_if`` holds against stored + just-submitted
    answers, so a hidden question is never required or stored on POST."""
    eval_answers = dict(stored)
    for q in page_questions:
        answered, value, error = _read_one(request, q)
        if answered and error is None:
            eval_answers[q.pk] = value
    return [q for q in page_questions if is_visible(q, eval_answers)]


def _renderable_questions(page_questions, stored):
    """Page questions to render on a GET: the currently-visible ones plus
    *latent* dependents — questions whose ``visible_if`` fails right now but
    references only controllers on THIS page, so answering the controller can
    reveal them without a round-trip. Latent questions are annotated with
    ``q.latent = True`` and rendered ``hidden disabled`` (a disabled fieldset
    is skipped by browser validation and never submits), which
    ``survey/js/branching.js`` toggles live; without JS the POST-side
    ``_visible_with_submitted`` recheck stays authoritative and reveals the
    dependent on the error re-render."""
    renderable = []
    # Controllers always precede their dependents (validated: same section,
    # strictly lower sort_order), so accumulating rendered ids in order lets a
    # latent question depend on another latent one (chained reveal). A
    # dependent whose controller is NOT rendered stays dropped — otherwise
    # branching.js would see an off-page controller and force-show it.
    rendered_ids = set()
    for q in page_questions:
        if is_visible(q, stored):
            q.latent = False
        else:
            refs = referenced_question_ids(q.visible_if or {})
            if not refs or not all(ref in rendered_ids for ref in refs):
                continue
            q.latent = True
        renderable.append(q)
        rendered_ids.add(q.pk)
    return renderable


def _next_renderable_stimulus_page(session, assignments, pages) -> str:
    """Advance the (assignment, page) cursors to the next page with at least one
    visible question, skipping pages hidden by branching. Returns ``"render"``
    (cursors point at a renderable page) or ``"demographics"`` (stimulus phase
    exhausted). Loops internally so the flow redirects at most once instead of
    once per skipped page."""
    while session.current_assignment_index < len(assignments):
        assignment = assignments[session.current_assignment_index]
        stored = _answers_for_stimulus(session, assignment.stimulus)
        while session.current_page_index < len(pages):
            if any(is_visible(q, stored) for q in pages[session.current_page_index]):
                return "render"
            session.current_page_index += 1
        session.current_page_index = 0
        session.current_assignment_index += 1
    return "demographics"


def _next_renderable_page(session, pages, index_attr: str, stored) -> str:
    """Advance the ``index_attr`` cursor on ``session`` to the next page that has
    at least one visible question (skipping pages fully hidden by branching).
    Returns ``"render"`` (cursor points at a renderable page) or ``"finish"``
    (the section is exhausted). Shared by the screening and demographic
    single-section flows."""
    while getattr(session, index_attr) < len(pages):
        idx = getattr(session, index_attr)
        if any(is_visible(q, stored) for q in pages[idx]):
            return "render"
        setattr(session, index_attr, idx + 1)
    return "finish"




def _ordered_section_questions(
    experiment: Experiment, section: str
) -> list[Question]:
    return list(
        experiment.questions.filter(section=section).order_by("sort_order", "pk")
    )


def _stimulus_questions(experiment: Experiment, session: ParticipantSession) -> list[Question]:
    """Return stimulus-section questions in per-session order.

    Shuffled with a session-seeded RNG when
    ``experiment.randomize_stimulus_questions`` is true (stable across
    refreshes); otherwise returned in the author-defined ``sort_order``.
    """
    cached = getattr(session, "_cached_question_order", None)
    if cached is None:
        questions = _ordered_section_questions(experiment, Question.Section.STIMULUS)
        # Skip-logic needs a deterministic order so a controlling question
        # always precedes its dependents; disable the shuffle when any
        # stimulus question carries a visible_if rule.
        has_branching = any(q.visible_if for q in questions)
        if experiment.randomize_stimulus_questions and not has_branching:
            ids_to_q = {q.pk: q for q in questions}
            ordered_ids = list(ids_to_q.keys())
            rng = random.Random(str(session.id))
            rng.shuffle(ordered_ids)
            cached = [ids_to_q[i] for i in ordered_ids]
        else:
            cached = questions
        session._cached_question_order = cached  # type: ignore[attr-defined]
    return cached  # type: ignore[return-value]


def _experiment_has_audio(experiment: Experiment) -> bool:
    return Stimulus.objects.filter(
        condition__experiment=experiment, kind=Stimulus.Kind.AUDIO
    ).exists()


def _audio_check_active(experiment: Experiment) -> bool:
    return bool(experiment.require_audio_check) and _experiment_has_audio(experiment)


def _has_screening(experiment: Experiment) -> bool:
    return experiment.questions.filter(
        section=Question.Section.SCREENING
    ).exists()


def _is_eligible(experiment: Experiment, answers: dict) -> bool:
    rule = experiment.eligibility_rule or {}
    if not rule:
        return True
    return evaluate_condition(rule, answers)


def _progress(
    experiment: Experiment, session: ParticipantSession
) -> int:
    dem_pages = len(
        paginate_questions(_ordered_section_questions(experiment, Question.Section.DEMOGRAPHIC))
    )
    screening_pages = len(
        paginate_questions(_ordered_section_questions(experiment, Question.Section.SCREENING))
    )
    audio_check = _audio_check_active(experiment)
    if experiment.is_pairwise:
        return pairwise_progress_percent(
            session,
            pairs_total=session.pair_assignments.count(),
            demographic_pages=dem_pages,
            audio_check=audio_check,
            screening_pages=screening_pages,
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
        screening_pages=screening_pages,
    )


def _now_ms() -> int:
    return int(timezone.now().timestamp() * 1000)


def _log_event(session, event_type, label="", elapsed_ms=None, **meta) -> None:
    """Append a raw flow event. Best-effort: never break the participant flow."""
    try:
        SurveyEvent.objects.create(
            session=session,
            event_type=event_type,
            label=label or "",
            elapsed_ms=elapsed_ms,
            meta=meta or {},
        )
    except Exception:  # pragma: no cover - logging must not interrupt the flow
        pass


def _page_elapsed_ms(request) -> int | None:
    """Server-measured dwell time from the page's ``_t0`` stamp (ms), or None.

    ``_t0`` is the server epoch-ms when the page was rendered (injected by
    ``_base_context``); both ends use the server clock. Absurd / negative
    deltas (clock skew, re-used tabs) are dropped rather than stored.
    """
    raw = request.POST.get("_t0")
    if not raw:
        return None
    try:
        delta = _now_ms() - int(raw)
    except (TypeError, ValueError):
        return None
    if delta < 0 or delta > 6 * 60 * 60 * 1000:  # > 6 hours → untrustworthy
        return None
    return delta


# Participant-facing name for each flow step, shown in the survey header so
# people always know where they are (the flow stays forward-only).
_STEP_LABELS = {
    ParticipantSession.Step.CONSENT: "Consent",
    ParticipantSession.Step.SCREENING: "Screening",
    ParticipantSession.Step.INSTRUCTIONS: "Instructions",
    ParticipantSession.Step.AUDIO_CHECK: "Audio check",
    ParticipantSession.Step.STIMULI: "Questions",
    ParticipantSession.Step.DEMOGRAPHICS: "About you",
}


def _base_context(experiment: Experiment, session: ParticipantSession | None) -> dict[str, Any]:
    ctx: dict[str, Any] = {
        "experiment": experiment,
        "brand": experiment.name,
        "is_test_mode": experiment.state == Experiment.State.TEST,
        "page_served_at": _now_ms(),
    }
    if session is not None:
        ctx["session"] = session
        ctx["progress_percent"] = _progress(experiment, session)
        ctx["step_label"] = _STEP_LABELS.get(session.last_step)
    else:
        ctx["progress_percent"] = 0
        ctx["step_label"] = _STEP_LABELS[ParticipantSession.Step.CONSENT]
    return ctx


def _resume_url(request, experiment: Experiment, session: ParticipantSession | None):
    """Absolute 'save & continue later' link, or None when not resumable."""
    if session is None or not session.resume_token or session.submitted_at:
        return None
    return request.build_absolute_uri(
        reverse(
            "survey:resume",
            kwargs={"slug": experiment.slug, "token": session.resume_token},
        )
    )


def _withdraw_url(request, slug: str, token) -> str | None:
    """Absolute 'withdraw & delete my data' link for a session token."""
    if not token:
        return None
    return request.build_absolute_uri(
        reverse("survey:withdraw", kwargs={"slug": slug, "token": token})
    )


# --- access gate (private studies) -----------------------------------------


def _access_gate(request, experiment: Experiment, slug: str):
    """Return a gate/blocked response for private studies, or None when access
    is granted (public studies, or a valid code / invite token)."""
    mode = experiment.access_mode
    if mode == Experiment.AccessMode.PUBLIC:
        return None
    granted_key = f"webeval:access:{slug}"
    if request.session.get(granted_key):
        return None
    if mode == Experiment.AccessMode.CODE:
        supplied = (request.GET.get("code") or "").strip()
        if supplied and supplied == experiment.access_code:
            request.session[granted_key] = True
            return None
        return redirect("survey:access", slug=slug)
    if mode == Experiment.AccessMode.INVITE:
        token = (request.GET.get("invite") or "").strip()
        if token:
            invite = ParticipantInvite.objects.filter(
                experiment=experiment, token=token, used_at__isnull=True
            ).first()
            if invite is not None:
                request.session[granted_key] = True
                request.session[f"webeval:invite:{slug}"] = token
                return None
        ctx = _base_context(experiment, None)
        ctx["progress_percent"] = None
        return render(request, "survey/invite_required.html", ctx, status=403)
    return None


def _consume_invite(request, experiment: Experiment, slug: str) -> None:
    """Mark the stashed single-use invite token as used (at session creation)."""
    if experiment.access_mode != Experiment.AccessMode.INVITE:
        return
    token = request.session.get(f"webeval:invite:{slug}")
    if token:
        ParticipantInvite.objects.filter(
            experiment=experiment, token=token, used_at__isnull=True
        ).update(used_at=timezone.now())


@require_http_methods(["GET", "POST"])
def access(request, slug: str):
    experiment = get_object_or_404(Experiment, slug=slug)
    if experiment.state not in RUNNABLE_STATES:
        return _unavailable(request, experiment)
    if experiment.access_mode != Experiment.AccessMode.CODE:
        return redirect("survey:consent", slug=slug)
    error = False
    if request.method == "POST":
        supplied = (request.POST.get("access_code") or "").strip()
        if experiment.access_code and supplied == experiment.access_code:
            request.session[f"webeval:access:{slug}"] = True
            return redirect("survey:consent", slug=slug)
        error = True
    ctx = _base_context(experiment, None)
    ctx["progress_percent"] = None
    ctx["error"] = error
    return render(request, "survey/access_code.html", ctx)


# --- consent ---------------------------------------------------------------


@require_http_methods(["GET", "POST"])
def consent(request, slug: str):
    experiment, session = _load_session(request, slug)
    if experiment.state not in RUNNABLE_STATES:
        return _unavailable(request, experiment)

    gate = _access_gate(request, experiment, slug)
    if gate is not None:
        return gate

    pid = request.COOKIES.get(PARTICIPANT_COOKIE) or uuid.uuid4().hex

    # Capture an external/platform id from the configured URL param; stash it in
    # the Django session so it survives the consent GET → POST.
    ext_key = f"webeval:extid:{slug}"
    if experiment.external_id_param:
        incoming = (request.GET.get(experiment.external_id_param) or "").strip()
        if incoming:
            request.session[ext_key] = incoming[:200]
    external_id = request.session.get(ext_key, "")

    def _with_pid(response):
        response.set_cookie(
            PARTICIPANT_COOKIE,
            pid,
            max_age=PARTICIPANT_COOKIE_MAX_AGE,
            samesite="Lax",
        )
        return response

    # Duplicate-submission gate: a study can allow only one completion per
    # participant (identified by the long-lived cookie).
    if experiment.one_submission_per_participant and _already_completed(
        experiment, pid
    ):
        return _with_pid(
            render(
                request,
                "survey/already_completed.html",
                _base_context(experiment, None),
            )
        )

    consent_first, consent_rest = _split_consent_text(experiment.consent_text)

    if request.method == "POST":
        if experiment.bot_protection and request.POST.get("hp_url", "").strip():
            # Honeypot filled → almost certainly a bot. Silently re-render the
            # consent page without creating a session.
            ctx = _base_context(experiment, session)
            ctx["consent_first"] = consent_first
            ctx["consent_rest"] = consent_rest
            return _with_pid(render(request, "survey/consent.html", ctx))
        if not request.POST.get("agree"):
            messages.error(
                request,
                "You must tick the consent checkbox to take part in the study.",
            )
            ctx = _base_context(experiment, session)
            ctx["error"] = True
            ctx["consent_first"] = consent_first
            ctx["consent_rest"] = consent_rest
            return _with_pid(render(request, "survey/consent.html", ctx))
        effective_uid = pid
        if experiment.collect_participant_code:
            code = (request.POST.get("participant_code") or "").strip()
            if not code:
                messages.error(
                    request,
                    f"Please enter your {experiment.participant_code_label.lower()}.",
                )
                ctx = _base_context(experiment, session)
                ctx["error"] = True
                ctx["consent_first"] = consent_first
                ctx["consent_rest"] = consent_rest
                return _with_pid(render(request, "survey/consent.html", ctx))
            if experiment.one_submission_per_participant and _already_completed(
                experiment, code
            ):
                return _with_pid(
                    render(
                        request,
                        "survey/already_completed.html",
                        _base_context(experiment, None),
                    )
                )
            longitudinal = _longitudinal_block(request, experiment, code)
            if longitudinal is not None:
                return _with_pid(longitudinal)
            effective_uid = code
        if session is None:
            session = _create_session(
                request,
                experiment,
                participant_uid=effective_uid,
                external_id=external_id,
            )
            _consume_invite(request, experiment, slug)
        session.consented_at = timezone.now()
        if _has_screening(experiment):
            session.last_step = ParticipantSession.Step.SCREENING
            session.screening_page_index = 0
            session.save(
                update_fields=["consented_at", "last_step", "screening_page_index"]
            )
            return _with_pid(redirect("survey:screening", slug=slug))
        needs_audio_check = _audio_check_active(experiment)
        session.last_step = (
            ParticipantSession.Step.AUDIO_CHECK
            if needs_audio_check
            else ParticipantSession.Step.INSTRUCTIONS
        )
        session.save(update_fields=["consented_at", "last_step"])
        if needs_audio_check:
            return _with_pid(redirect("survey:audio_check", slug=slug))
        return _with_pid(redirect("survey:instructions", slug=slug))

    Experiment.objects.filter(pk=experiment.pk).update(
        consent_page_views=F("consent_page_views") + 1
    )
    ctx = _base_context(experiment, session)
    ctx["consent_first"] = consent_first
    ctx["consent_rest"] = consent_rest
    return _with_pid(render(request, "survey/consent.html", ctx))


def _already_completed(experiment: Experiment, pid: str) -> bool:
    if not pid:
        return False
    return ParticipantSession.objects.filter(
        experiment=experiment, participant_uid=pid, submitted_at__isnull=False
    ).exists()


def _longitudinal_block(request, experiment: Experiment, code: str):
    """For a follow-up phase, return an explanatory page (or None) until the
    participant has completed the predecessor phase and the spacing gap has
    elapsed — or if they have already completed this phase."""
    if experiment.follows_id is None:
        return None
    base = {**_base_context(experiment, None), "progress_percent": None}
    if ParticipantSession.objects.filter(
        experiment=experiment, participant_uid=code, submitted_at__isnull=False
    ).exists():
        return render(request, "survey/already_completed.html", base)
    predecessor = experiment.follows
    prior = (
        ParticipantSession.objects.filter(
            experiment=predecessor, participant_uid=code, submitted_at__isnull=False
        )
        .order_by("-submitted_at")
        .first()
    )
    ctx = {**base, "predecessor": predecessor}
    if prior is None:
        return render(
            request, "survey/phase_locked.html", {**ctx, "reason": "predecessor"}
        )
    if experiment.phase_gap_hours:
        opens_at = prior.submitted_at + timedelta(hours=experiment.phase_gap_hours)
        if timezone.now() < opens_at:
            return render(
                request,
                "survey/phase_locked.html",
                {**ctx, "reason": "too_early", "opens_at": opens_at},
            )
    return None


def _create_session(
    request,
    experiment: Experiment,
    participant_uid: str = "",
    external_id: str = "",
) -> ParticipantSession:
    meta = extract_metadata(request)
    session = ParticipantSession.objects.create(
        experiment=experiment,
        device_type=meta.device_type,
        browser_family=meta.browser_family,
        country_code=meta.country_code,
        resume_token=secrets.token_urlsafe(32),
        participant_uid=participant_uid,
        external_id=external_id,
        is_preview=experiment.state == Experiment.State.TEST,
        consent_version=experiment.consent_version,
    )
    request.session[_session_key(experiment.slug)] = str(session.id)
    _log_event(session, SurveyEvent.Type.STARTED)
    return session


# --- screening / eligibility ------------------------------------------------


@dataclass(frozen=True)
class _PagedSection:
    """Declarative config for a single-section paged flow (screening or
    demographics). The engine ``_run_paged_section`` is the one implementation;
    ``play`` stays bespoke because of its per-stimulus assignment cursors and
    stimulus-media context."""

    step: str            # ParticipantSession.Step this view serves
    section: str         # Question.Section it paginates
    cursor: str          # session integer-cursor attribute name
    template: str
    url_name: str        # this view's own URL (for same-page redirects)
    finish_fn: Callable  # (request, session, slug) when the section is exhausted
    empty_fn: Callable   # (request, session, slug) when there are no pages
    log_label: str | None = None  # SurveyEvent.PAGE_SUBMIT label, or None to skip


def _section_page_ctx(request, experiment, session, cfg, pages, visible, idx):
    ctx = _base_context(experiment, session)
    ctx.update({
        "page_questions": visible,
        "is_last_page": idx == len(pages) - 1,
        "page_number": idx + 1,
        "page_total": len(pages),
        "resume_url": _resume_url(request, experiment, session),
        "withdraw_url": _withdraw_url(request, experiment.slug, session.resume_token),
    })
    return ctx


def _run_paged_section(request, experiment, session, slug, cfg: _PagedSection):
    """Shared engine for screening + demographics: guard the step, paginate the
    section, submit-or-render one page, and advance the cursor over pages hidden
    by branching."""
    bounce = _expect_step(session, cfg.step)
    if bounce:
        return bounce

    pages = paginate_questions(_ordered_section_questions(experiment, cfg.section))
    if not pages:
        return cfg.empty_fn(request, session, slug)

    if request.method == "POST":
        if getattr(session, cfg.cursor) >= len(pages):
            return redirect(cfg.url_name, slug=slug)
        idx = getattr(session, cfg.cursor)
        stored = _answers_for_section(session, cfg.section)
        visible = _visible_with_submitted(request, pages[idx], stored)
        errors, responses = _collect_answers(request, session, visible)
        if errors:
            for err in errors:
                messages.error(request, err)
            _annotate_submitted(request, visible)
            ctx = _section_page_ctx(request, experiment, session, cfg, pages, visible, idx)
            return render(request, cfg.template, ctx, status=400)
        with transaction.atomic():
            Response.objects.bulk_create(responses)
            if cfg.log_label:
                _log_event(
                    session, SurveyEvent.Type.PAGE_SUBMIT,
                    label=cfg.log_label, elapsed_ms=_page_elapsed_ms(request),
                )
            setattr(session, cfg.cursor, idx + 1)
            after = _answers_for_section(session, cfg.section)
            if _next_renderable_page(session, pages, cfg.cursor, after) == "finish":
                return cfg.finish_fn(request, session, slug)
            session.save(update_fields=[cfg.cursor])
        return redirect(cfg.url_name, slug=slug)

    # GET: advance over pages hidden by branching (or finish the section).
    stored = _answers_for_section(session, cfg.section)
    if _next_renderable_page(session, pages, cfg.cursor, stored) == "finish":
        return cfg.finish_fn(request, session, slug)
    session.save(update_fields=[cfg.cursor])
    idx = getattr(session, cfg.cursor)
    visible = _renderable_questions(pages[idx], _answers_for_section(session, cfg.section))
    ctx = _section_page_ctx(request, experiment, session, cfg, pages, visible, idx)
    return render(request, cfg.template, ctx)


@require_http_methods(["GET", "POST"])
def screening(request, slug: str):
    experiment, session = _load_session(request, slug)
    if experiment.state not in RUNNABLE_STATES:
        return _unavailable(request, experiment)
    if session is None:
        return redirect("survey:consent", slug=slug)
    return _run_paged_section(
        request, experiment, session, slug,
        _PagedSection(
            step=ParticipantSession.Step.SCREENING,
            section=Question.Section.SCREENING,
            cursor="screening_page_index",
            template="survey/screening.html",
            url_name="survey:screening",
            finish_fn=_finish_screening,
            empty_fn=_advance_past_screening,
        ),
    )


def _finish_screening(request, session: ParticipantSession, slug: str):
    experiment = session.experiment
    answers = _answers_for_section(session, Question.Section.SCREENING)
    if not _is_eligible(experiment, answers):
        session.last_step = ParticipantSession.Step.SCREENED_OUT
        session.screened_out_at = timezone.now()
        session.save(update_fields=["last_step", "screened_out_at"])
        _log_event(session, SurveyEvent.Type.SCREENED_OUT)
        request.session.pop(_session_key(slug), None)
        return redirect("survey:screened_out", slug=slug)
    return _advance_past_screening(request, session, slug)


def _advance_past_screening(request, session: ParticipantSession, slug: str):
    experiment = session.experiment
    if _audio_check_active(experiment):
        session.last_step = ParticipantSession.Step.AUDIO_CHECK
        session.save(update_fields=["last_step"])
        return redirect("survey:audio_check", slug=slug)
    session.last_step = ParticipantSession.Step.INSTRUCTIONS
    session.save(update_fields=["last_step"])
    return redirect("survey:instructions", slug=slug)


# --- instructions ----------------------------------------------------------


@require_http_methods(["GET", "POST"])
def instructions(request, slug: str):
    experiment, session = _load_session(request, slug)
    if experiment.state not in RUNNABLE_STATES:
        return _unavailable(request, experiment)
    if session is None:
        return redirect("survey:consent", slug=slug)
    bounce = _expect_step(session, ParticipantSession.Step.INSTRUCTIONS)
    if bounce:
        return bounce

    if request.method == "POST":
        is_pairwise = experiment.is_pairwise
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
    experiment = session.experiment
    try:
        strategy = get_strategy(experiment.assignment_strategy)
    except UnknownStrategyError:
        # Never block a live participant, but leave a trace: falling back
        # silently would swap the study design without anyone noticing.
        logger.warning(
            "Experiment %s: unknown assignment strategy %r — falling back "
            "to balanced_random.",
            experiment.slug,
            experiment.assignment_strategy,
        )
        strategy = get_strategy("balanced_random")
    counts = _fetch_counts(experiment)
    # Ordinal of this participant among those already assigned — lets the
    # counterbalanced / between-subject strategies balance across the sample.
    participant_index = (
        ParticipantSession.objects.filter(
            experiment=experiment, assignments__isnull=False
        )
        .distinct()
        .count()
    )
    selected: list[Stimulus] = strategy.select(
        experiment=experiment,
        n=experiment.stimuli_per_participant,
        counts=counts,
        rng=random.Random(str(session.id)),
        participant_index=participant_index,
    )
    # Between-subject designs assign each participant to one condition; record
    # it when the selection is single-condition so analysis can group by it.
    condition_ids = {stim.condition_id for stim in selected}
    if len(condition_ids) == 1:
        session.assigned_condition_id = next(iter(condition_ids))
        session.save(update_fields=["assigned_condition"])
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
    if experiment.state not in RUNNABLE_STATES:
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
    if experiment.state not in RUNNABLE_STATES:
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

    # POST submits the page the participant is currently viewing.
    if request.method == "POST":
        if session.current_page_index >= len(pages):
            return redirect("survey:play", slug=slug)
        return _save_page_answers(
            request,
            session,
            assignment,
            pages[session.current_page_index],
            pages,
            assignments,
            slug,
        )

    # GET: advance over pages hidden by branching to the next renderable one.
    if _next_renderable_stimulus_page(session, assignments, pages) == "demographics":
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
    session.save(update_fields=["current_page_index", "current_assignment_index"])

    assignment = assignments[session.current_assignment_index]
    page_questions = pages[session.current_page_index]
    stored = _answers_for_stimulus(session, assignment.stimulus)
    visible_questions = _renderable_questions(page_questions, stored)
    is_last_page = (
        session.current_assignment_index == len(assignments) - 1
        and session.current_page_index == len(pages) - 1
    )

    ctx = _base_context(experiment, session)
    ctx.update(
        {
            "assignment": assignment,
            "stimulus": assignment.stimulus,
            "page_questions": visible_questions,
            "is_last_page": is_last_page,
            "has_more_after_stimuli": _ordered_section_questions(
                experiment, Question.Section.DEMOGRAPHIC
            ),
            # Latent questions count too: if branching.js reveals one that
            # wants the stimulus prompt, the prompt block must already exist.
            "show_prompt": any(q.show_prompt for q in visible_questions),
            "page_number": session.current_assignment_index * len(pages)
            + session.current_page_index
            + 1,
            "page_total": len(assignments) * len(pages),
            "resume_url": _resume_url(request, experiment, session),
            "withdraw_url": _withdraw_url(request, experiment.slug, session.resume_token),
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
    stored = _answers_for_stimulus(session, assignment.stimulus)
    visible_questions = _visible_with_submitted(request, page_questions, stored)
    errors, responses = _collect_answers(
        request, session, visible_questions, stimulus=assignment.stimulus
    )
    if errors:
        for err in errors:
            messages.error(request, err)
        experiment = session.experiment
        _annotate_submitted(request, visible_questions)
        ctx = _base_context(experiment, session)
        ctx.update(
            {
                "assignment": assignment,
                "stimulus": assignment.stimulus,
                "page_questions": visible_questions,
                "is_last_page": (
                    session.current_assignment_index == len(assignments) - 1
                    and session.current_page_index == len(pages) - 1
                ),
                "show_prompt": any(q.show_prompt for q in visible_questions),
                "page_number": session.current_assignment_index * len(pages)
                + session.current_page_index
                + 1,
                "page_total": len(assignments) * len(pages),
            }
        )
        return render(request, "survey/play.html", ctx, status=400)

    with transaction.atomic():
        Response.objects.bulk_create(responses)
        _log_event(
            session, SurveyEvent.Type.PAGE_SUBMIT, label="stimulus",
            elapsed_ms=_page_elapsed_ms(request),
        )
        session.current_page_index += 1
        if _next_renderable_stimulus_page(session, assignments, pages) == "demographics":
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
        cfg = q.config or {}
        if q.type == Question.Type.MATRIX:
            rows = cfg.get("rows") or []
            q.submitted_matrix = {
                i: request.POST.get(f"{key}_r{i}", "") for i in range(len(rows))
            }
        elif q.type == Question.Type.RANKING:
            items = cfg.get("items") or []
            q.submitted_ranks = {
                i: request.POST.get(f"{key}_i{i}", "") for i in range(len(items))
            }
        elif q.type == Question.Type.CHOICE and cfg.get("multi"):
            q.submitted_values = request.POST.getlist(key)
        else:
            q.submitted_value = request.POST.get(key, "")


def _collect_answers(
    request,
    session: ParticipantSession,
    questions: list[Question],
    *,
    stimulus: Stimulus | None = None,
    pair: "PairAssignment | None" = None,
) -> tuple[list[str], list[Response]]:
    """Read and validate a page of answers, returning (errors, unsaved Response
    rows). ``stimulus`` (standard flow) or ``pair`` (pairwise flow) ties each
    answer to its target; both flows are otherwise identical."""
    errors: list[str] = []
    responses: list[Response] = []
    elapsed = _page_elapsed_ms(request)
    for q in questions:
        answered, value, error = _read_one(request, q)
        if error is not None:
            errors.append(f"'{q.prompt}' {error}.")
            continue
        if not answered:
            if q.required:
                errors.append(f"'{q.prompt}' is required.")
            continue
        responses.append(
            Response(
                session=session,
                stimulus=stimulus,
                pair_assignment=pair,
                question=q,
                answer_value=json.dumps(value, ensure_ascii=False),
                elapsed_ms=elapsed,
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
        # "balanced_random" is the untouched model default on a pairwise
        # study — mapping it to the pairwise default is expected, not a
        # misconfiguration worth logging on every session.
        if session.experiment.assignment_strategy != "balanced_random":
            logger.warning(
                "Experiment %s: unknown pairwise strategy %r — falling back "
                "to pairwise_balanced.",
                session.experiment.slug,
                session.experiment.assignment_strategy,
            )
        strategy = get_pairwise_strategy("pairwise_balanced")
    pair_counts = _fetch_pair_counts(session.experiment)
    stimulus_counts = _fetch_stimulus_counts(session.experiment)
    specs = strategy.select_pairs(
        experiment=session.experiment,
        n=session.experiment.stimuli_per_participant,
        pair_counts=pair_counts,
        stimulus_counts=stimulus_counts,
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


def _fetch_stimulus_counts(experiment: Experiment) -> dict[int, int]:
    from django.db.models import Count

    counts: dict[int, int] = {}
    for field in ("stimulus_a_id", "stimulus_b_id"):
        rows = (
            PairAssignment.objects.filter(session__experiment=experiment)
            .values(field)
            .annotate(n=Count("pk"))
        )
        for row in rows:
            counts[row[field]] = counts.get(row[field], 0) + row["n"]
    return counts


# --- pairwise play ---------------------------------------------------------


@require_http_methods(["GET", "POST"])
def pairwise_play(request, slug: str):
    experiment, session = _load_session(request, slug)
    if experiment.state not in RUNNABLE_STATES:
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
    prompt = _prompt_for_pair(experiment, pair)

    # The template context is identical on the POST-error re-render and the GET
    # render, so build it once.
    def _pairwise_ctx():
        ctx = _base_context(experiment, session)
        ctx.update({
            "pair": pair,
            "prompt": prompt,
            "questions": questions,
            "is_last_pair": is_last_pair,
            "has_demographics": has_demographics,
            "pair_number": session.current_pair_index + 1,
            "pairs_total": len(pairs),
            "page_number": session.current_pair_index + 1,
            "page_total": len(pairs),
            "page_noun": "Comparison",
            "show_prompt": any(q.show_prompt for q in questions),
        })
        return ctx

    if request.method == "POST":
        errors, responses = _collect_answers(
            request, session,
            _visible_with_submitted(request, questions, {}),
            pair=pair,
        )
        if errors:
            for err in errors:
                messages.error(request, err)
            _annotate_submitted(request, questions)
            return render(
                request, "survey/pairwise_play.html", _pairwise_ctx(), status=400
            )

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

    return render(request, "survey/pairwise_play.html", _pairwise_ctx())


def _prompt_for_pair(experiment: Experiment, pair: PairAssignment) -> Prompt | None:
    """Return the audio ``Prompt`` for ``pair`` in a PAIRWISE_AUDIO experiment.

    Returns None for plain PAIRWISE experiments (where the prompt is text).
    """
    if experiment.mode != Experiment.Mode.PAIRWISE_AUDIO:
        return None
    if not pair.prompt_group:
        return None
    return Prompt.objects.filter(
        experiment=experiment, prompt_group=pair.prompt_group
    ).first()


# --- listen duration endpoint ----------------------------------------------


# csrf_exempt: the audio tracker's primary transport is navigator.sendBeacon,
# which cannot carry the X-CSRFToken header (with CSRF enforced every beacon
# would 403 and listen durations would silently stay 0). Exempting is safe
# here: the endpoint is scoped to the participant's own session cookie, takes
# no sensitive action, and only ever raises the stored maximum.
@csrf_exempt
@require_POST
def record_listen(request, slug: str, assignment_id: int):
    _, session = _load_session(request, slug)
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


# csrf_exempt for the same reason as record_listen (sendBeacon transport).
@csrf_exempt
@require_POST
def record_listen_pair(request, slug: str, pair_id: int):
    _, session = _load_session(request, slug)
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
    elif side == "prompt":
        pair.listen_duration_prompt_ms = max(
            pair.listen_duration_prompt_ms, duration_ms
        )
        pair.save(update_fields=["listen_duration_prompt_ms"])
    else:
        return HttpResponseBadRequest("side must be 'a', 'b', or 'prompt'")
    return JsonResponse({"ok": True})


# --- demographics ----------------------------------------------------------


@require_http_methods(["GET", "POST"])
def demographics(request, slug: str):
    experiment, session = _load_session(request, slug)
    if experiment.state not in RUNNABLE_STATES:
        return _unavailable(request, experiment)
    if session is None:
        return redirect("survey:consent", slug=slug)
    return _run_paged_section(
        request, experiment, session, slug,
        _PagedSection(
            step=ParticipantSession.Step.DEMOGRAPHICS,
            section=Question.Section.DEMOGRAPHIC,
            cursor="demographic_page_index",
            template="survey/demographics.html",
            url_name="survey:demographics",
            finish_fn=_finish_session,
            empty_fn=_finish_session,
            log_label="demographic",
        ),
    )


def _completion_code_for(session: ParticipantSession) -> str:
    experiment = session.experiment
    mode = experiment.completion_code_mode
    if mode == Experiment.CompletionCodeMode.FIXED:
        return experiment.completion_code
    if mode == Experiment.CompletionCodeMode.UNIQUE:
        return secrets.token_hex(6).upper()
    return ""


def _finish_session(request, session: ParticipantSession, slug: str):
    session.submitted_at = timezone.now()
    session.last_step = ParticipantSession.Step.DONE
    session.completion_code = _completion_code_for(session)
    _log_event(session, SurveyEvent.Type.COMPLETED)
    failed, flags = compute_flags(session)
    session.failed_attention_checks = failed
    session.flags = flags
    session.save(
        update_fields=[
            "submitted_at",
            "last_step",
            "demographic_page_index",
            "failed_attention_checks",
            "flags",
            "completion_code",
        ]
    )
    if session.completion_code:
        request.session[f"webeval:code:{slug}"] = session.completion_code
    if session.resume_token:
        request.session[f"webeval:token:{slug}"] = session.resume_token
    if session.participant_uid:
        request.session[f"webeval:uid:{slug}"] = session.participant_uid
    request.session.pop(_session_key(slug), None)
    _dispatch_completion_hooks(session)
    return redirect("survey:thanks", slug=slug)


def _dispatch_completion_hooks(session: ParticipantSession) -> None:
    """Fire outbound webhooks + the operator notification on completion.

    Best-effort: integration failures must never break the participant's
    completion (which has already been persisted by this point)."""
    try:
        from experiments.webhooks import deliver_event

        deliver_event(session, "session.completed")
    except Exception:  # pragma: no cover - defensive
        pass
    email = session.experiment.notify_email
    if email:
        try:
            send_mail(
                subject=f"[webeval] A participant completed '{session.experiment.name}'",
                message=(
                    f"A participant just completed '{session.experiment.name}'.\n"
                    f"Session: {session.id}\n"
                    f"Completed at: {session.submitted_at:%Y-%m-%d %H:%M} UTC\n"
                ),
                from_email=None,
                recipient_list=[email],
                fail_silently=True,
            )
        except Exception:  # pragma: no cover - defensive
            pass


# --- thanks ---------------------------------------------------------------


@require_http_methods(["GET"])
def screened_out(request, slug: str):
    experiment = get_object_or_404(Experiment, slug=slug)
    ctx = _base_context(experiment, None)
    ctx["progress_percent"] = None  # no progress bar on the screen-out page
    return render(request, "survey/screened_out.html", ctx)


@require_http_methods(["GET"])
def resume(request, slug: str, token: str):
    """Re-enter an in-progress session from a 'save & continue later' link.

    Looks the session up by its secret ``resume_token`` (cookie-independent, so
    it works on another device), re-establishes the session cookie, and bounces
    to the step the participant left off on.
    """
    experiment = get_object_or_404(Experiment, slug=slug)
    session = ParticipantSession.objects.filter(
        experiment=experiment, resume_token=token
    ).first()
    if session is None:
        return render(
            request,
            "survey/resume_invalid.html",
            {"experiment": experiment, "brand": experiment.name},
            status=404,
        )
    if session.submitted_at is not None:
        request.session.pop(_session_key(slug), None)
        return redirect("survey:thanks", slug=slug)
    request.session[_session_key(slug)] = str(session.id)
    return redirect(required_step_url(session))


def _withdraw_data(session: ParticipantSession) -> None:
    """Erase a participant's answers + behavioural data, leaving an anonymised
    tombstone recording that a withdrawal happened (no personal data retained)."""
    Response.objects.filter(session=session).delete()
    StimulusAssignment.objects.filter(session=session).delete()
    PairAssignment.objects.filter(session=session).delete()
    SurveyEvent.objects.filter(session=session).delete()
    session.withdrawn_at = timezone.now()
    session.last_step = ParticipantSession.Step.WITHDRAWN
    session.submitted_at = None
    session.completion_code = ""
    session.external_id = ""
    session.participant_uid = ""
    session.country_code = ""
    session.device_type = ""
    session.browser_family = ""
    session.assigned_condition = None
    session.flags = []
    session.failed_attention_checks = 0
    session.resume_token = None
    session.save()


@require_http_methods(["GET", "POST"])
def withdraw(request, slug: str, token: str):
    """Participant-visible withdrawal + data deletion via their session token."""
    experiment = get_object_or_404(Experiment, slug=slug)
    session = ParticipantSession.objects.filter(
        experiment=experiment, resume_token=token
    ).first()
    if session is None:
        # Unknown or already-used token (e.g. data already withdrawn).
        return render(
            request,
            "survey/withdrawn.html",
            {
                "experiment": experiment,
                "brand": experiment.name,
                "progress_percent": None,
                "already": True,
            },
        )
    if request.method == "POST":
        _withdraw_data(session)
        request.session.pop(_session_key(slug), None)
        return render(
            request,
            "survey/withdrawn.html",
            {
                "experiment": experiment,
                "brand": experiment.name,
                "progress_percent": None,
            },
        )
    ctx = _base_context(experiment, session)
    ctx["progress_percent"] = None
    return render(request, "survey/withdraw_confirm.html", ctx)


@require_http_methods(["GET"])
def thanks(request, slug: str):
    experiment = get_object_or_404(Experiment, slug=slug)
    completion_code = request.session.pop(f"webeval:code:{slug}", "")
    token = request.session.pop(f"webeval:token:{slug}", "")
    participant_code = request.session.pop(f"webeval:uid:{slug}", "")

    next_phase = experiment.next_phases.order_by("created_at").first()
    next_phase_info = None
    if next_phase is not None:
        next_phase_info = {
            "name": next_phase.name,
            "url": request.build_absolute_uri(
                reverse("survey:consent", kwargs={"slug": next_phase.slug})
            ),
            "code": participant_code,
            "opens_at": (
                timezone.now() + timedelta(hours=next_phase.phase_gap_hours)
                if next_phase.phase_gap_hours
                else None
            ),
        }

    return render(
        request,
        "survey/thanks.html",
        {
            "experiment": experiment,
            "brand": experiment.name,
            "progress_percent": 100,
            "is_test_mode": experiment.state == Experiment.State.TEST,
            "completion_code": completion_code,
            "withdraw_url": _withdraw_url(request, slug, token),
            "next_phase": next_phase_info,
        },
    )
