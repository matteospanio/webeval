"""CSV exports for experiments (served from admin custom URLs).

Two shapes are produced:

* **answers_csv** — long format, one row per answered stimulus-question
  (demographics excluded). Sessions without a ``submitted_at`` timestamp
  are dropped so abandoned sessions never leak into analysis spreadsheets.

* **demographics_csv** — wide format, one row per completed session, with
  one dynamic column per demographic :class:`Question` (named ``q_<pk>``).
  Session-level metadata (device, browser, country) also gets its own
  columns.
"""
from __future__ import annotations

import csv

from django.http import HttpResponse

from experiments.models import Experiment, Question
from survey.models import PairAssignment, ParticipantSession, Response


ANSWER_FIELDNAMES = [
    "session_id",
    "submitted_at",
    "experiment",
    "stimulus_id",
    "condition",
    "question_id",
    "question_type",
    "answer_value",
    "listen_duration_ms",
]


def _csv_response(filename: str) -> HttpResponse:
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


def write_answers_csv(experiment: Experiment, response: HttpResponse) -> HttpResponse:
    writer = csv.DictWriter(response, fieldnames=ANSWER_FIELDNAMES)
    writer.writeheader()

    rows = (
        Response.objects.filter(
            session__experiment=experiment,
            session__submitted_at__isnull=False,
            stimulus__isnull=False,
        )
        .select_related("session", "stimulus", "stimulus__condition", "question")
        .order_by("session__id", "stimulus__sort_order", "question__sort_order")
    )

    # Pre-compute assignment listen durations per (session, stimulus) so we
    # don't issue one query per row.
    assignment_map: dict[tuple[str, int], int] = {}
    for sess_id, stim_id, ms in (
        ParticipantSession.objects.filter(
            experiment=experiment, submitted_at__isnull=False
        )
        .values_list("id", "assignments__stimulus_id", "assignments__listen_duration_ms")
    ):
        if stim_id is not None:
            assignment_map[(str(sess_id), stim_id)] = ms or 0

    for r in rows:
        writer.writerow(
            {
                "session_id": str(r.session_id),
                "submitted_at": r.session.submitted_at.isoformat()
                if r.session.submitted_at
                else "",
                "experiment": experiment.slug,
                "stimulus_id": r.stimulus_id or "",
                "condition": r.stimulus.condition.name if r.stimulus_id else "",
                "question_id": r.question_id,
                "question_type": r.question.type,
                "answer_value": r.answer_value,
                "listen_duration_ms": assignment_map.get(
                    (str(r.session_id), r.stimulus_id), 0
                ),
            }
        )
    return response


def write_demographics_csv(
    experiment: Experiment, response: HttpResponse
) -> HttpResponse:
    demographic_questions = list(
        experiment.questions.filter(section=Question.Section.DEMOGRAPHIC).order_by(
            "sort_order", "id"
        )
    )
    fieldnames = [
        "session_id",
        "submitted_at",
        "experiment",
        "device_type",
        "browser_family",
        "country_code",
    ] + [f"q_{q.pk}" for q in demographic_questions]

    writer = csv.DictWriter(response, fieldnames=fieldnames)
    writer.writeheader()

    sessions = ParticipantSession.objects.filter(
        experiment=experiment, submitted_at__isnull=False
    ).order_by("started_at")
    for session in sessions:
        row = {
            "session_id": str(session.id),
            "submitted_at": session.submitted_at.isoformat()
            if session.submitted_at
            else "",
            "experiment": experiment.slug,
            "device_type": session.device_type,
            "browser_family": session.browser_family,
            "country_code": session.country_code,
        }
        answers = {
            r.question_id: r.answer_value
            for r in Response.objects.filter(
                session=session, question__in=demographic_questions
            )
        }
        for q in demographic_questions:
            row[f"q_{q.pk}"] = answers.get(q.pk, "")
        writer.writerow(row)
    return response


PAIRWISE_FIELDNAMES = [
    "session_id",
    "submitted_at",
    "experiment",
    "pair_index",
    "model_a",
    "model_b",
    "prompt_group",
    "position_a",
    "question_id",
    "question_prompt",
    "preferred",
    "listen_duration_a_ms",
    "listen_duration_b_ms",
]


def write_pairwise_answers_csv(
    experiment: Experiment, response: HttpResponse
) -> HttpResponse:
    """Bradley-Terry format: one row per attribute per pair per session."""
    writer = csv.DictWriter(response, fieldnames=PAIRWISE_FIELDNAMES)
    writer.writeheader()

    rows = (
        Response.objects.filter(
            session__experiment=experiment,
            session__submitted_at__isnull=False,
            pair_assignment__isnull=False,
        )
        .select_related(
            "session",
            "pair_assignment",
            "pair_assignment__stimulus_a__condition",
            "pair_assignment__stimulus_b__condition",
            "question",
        )
        .order_by("session__id", "pair_assignment__sort_order", "question__sort_order")
    )

    for r in rows:
        pa = r.pair_assignment
        writer.writerow(
            {
                "session_id": str(r.session_id),
                "submitted_at": r.session.submitted_at.isoformat()
                if r.session.submitted_at
                else "",
                "experiment": experiment.slug,
                "pair_index": pa.sort_order,
                "model_a": pa.stimulus_a.condition.name,
                "model_b": pa.stimulus_b.condition.name,
                "prompt_group": pa.prompt_group,
                "position_a": pa.position_a,
                "question_id": r.question_id,
                "question_prompt": r.question.prompt[:100],
                "preferred": r.answer_value,
                "listen_duration_a_ms": pa.listen_duration_a_ms,
                "listen_duration_b_ms": pa.listen_duration_b_ms,
            }
        )
    return response


def pairwise_answers_csv_response(experiment: Experiment) -> HttpResponse:
    response = _csv_response(f"{experiment.slug}-pairwise-answers.csv")
    return write_pairwise_answers_csv(experiment, response)


def answers_csv_response(experiment: Experiment) -> HttpResponse:
    response = _csv_response(f"{experiment.slug}-answers.csv")
    return write_answers_csv(experiment, response)


def demographics_csv_response(experiment: Experiment) -> HttpResponse:
    response = _csv_response(f"{experiment.slug}-demographics.csv")
    return write_demographics_csv(experiment, response)
