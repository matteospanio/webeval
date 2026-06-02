"""Attention checks + suspicious-session flags, and exclusion from exports."""
from __future__ import annotations

import csv as csvmod
import io
import json
import re
from datetime import timedelta

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from experiments.csv_exports import answers_csv_response
from experiments.models import Experiment, Question
from experiments.tests.factories import (
    ChoiceQuestionFactory,
    ConditionFactory,
    ExperimentFactory,
    RatingQuestionFactory,
    StimulusFactory,
    TextStimulusFactory,
)
from survey.flagging import compute_flags
from survey.models import ParticipantSession, Response

pytestmark = pytest.mark.django_db


def _completed_session(exp, elapsed_seconds=120):
    now = timezone.now()
    return ParticipantSession.objects.create(
        experiment=exp,
        last_step=ParticipantSession.Step.DONE,
        consented_at=now - timedelta(seconds=elapsed_seconds),
        submitted_at=now,
    )


# --- compute_flags unit tests ----------------------------------------------


def test_attention_check_failure_counted():
    exp = ExperimentFactory()
    q = ChoiceQuestionFactory(
        experiment=exp,
        attention_expected="Yes",
        config={"choices": ["Yes", "No"], "multi": False},
    )
    s = _completed_session(exp)
    Response.objects.create(session=s, stimulus=None, question=q, answer_value=json.dumps("No"))
    failed, flags = compute_flags(s)
    assert failed == 1 and "failed_attention" in flags


def test_attention_check_pass_not_flagged():
    exp = ExperimentFactory()
    q = ChoiceQuestionFactory(
        experiment=exp,
        attention_expected="Yes",
        config={"choices": ["Yes", "No"], "multi": False},
    )
    s = _completed_session(exp)
    Response.objects.create(session=s, stimulus=None, question=q, answer_value=json.dumps("Yes"))
    failed, flags = compute_flags(s)
    assert failed == 0 and "failed_attention" not in flags


def test_speeder_flag():
    exp = ExperimentFactory(min_completion_seconds=60)
    _, flags = compute_flags(_completed_session(exp, elapsed_seconds=10))
    assert "speeder" in flags


def test_not_speeder_above_threshold():
    exp = ExperimentFactory(min_completion_seconds=60)
    _, flags = compute_flags(_completed_session(exp, elapsed_seconds=120))
    assert "speeder" not in flags


def test_straight_lining_flag():
    exp = ExperimentFactory()
    cond = ConditionFactory(experiment=exp)
    q = RatingQuestionFactory(experiment=exp, section=Question.Section.STIMULUS)
    s = _completed_session(exp)
    for i in range(3):
        stim = StimulusFactory(condition=cond, title=f"s{i}")
        Response.objects.create(session=s, stimulus=stim, question=q, answer_value=json.dumps(50))
    _, flags = compute_flags(s)
    assert "straight_lining" in flags


def test_no_straight_lining_when_varied():
    exp = ExperimentFactory()
    cond = ConditionFactory(experiment=exp)
    q = RatingQuestionFactory(experiment=exp, section=Question.Section.STIMULUS)
    s = _completed_session(exp)
    for i, val in enumerate((30, 50, 70)):
        stim = StimulusFactory(condition=cond, title=f"s{i}")
        Response.objects.create(session=s, stimulus=stim, question=q, answer_value=json.dumps(val))
    _, flags = compute_flags(s)
    assert "straight_lining" not in flags


# --- flow + export ---------------------------------------------------------


def test_attention_failure_flagged_in_flow_and_excluded_from_csv():
    exp = ExperimentFactory(slug="acflow", require_audio_check=False)
    cond = ConditionFactory(experiment=exp, name="C")
    TextStimulusFactory(condition=cond, title="t", sort_order=0)
    RatingQuestionFactory(experiment=exp, sort_order=0)  # stimulus question
    ac = ChoiceQuestionFactory(
        experiment=exp,
        section=Question.Section.DEMOGRAPHIC,
        prompt="Pick Yes",
        sort_order=0,
        config={"choices": ["Yes", "No"], "multi": False},
        attention_expected="Yes",
    )
    exp.state = Experiment.State.ACTIVE
    exp.save(update_fields=["state"])

    client = Client()
    client.post(reverse("survey:consent", kwargs={"slug": exp.slug}), {"agree": "on"})
    client.post(reverse("survey:instructions", kwargs={"slug": exp.slug}))
    play = reverse("survey:play", kwargs={"slug": exp.slug})
    body = client.get(play).content.decode()
    (qid,) = {int(x) for x in re.findall(r'name="q_(\d+)', body)}
    client.post(play, {f"q_{qid}": "50"})
    client.post(
        reverse("survey:demographics", kwargs={"slug": exp.slug}),
        {f"q_{ac.pk}": "No"},
        follow=True,
    )

    session = ParticipantSession.objects.get()
    assert session.submitted_at is not None
    assert session.failed_attention_checks == 1
    assert "failed_attention" in session.flags

    full = list(csvmod.DictReader(io.StringIO(answers_csv_response(exp).content.decode())))
    excluded = list(
        csvmod.DictReader(
            io.StringIO(answers_csv_response(exp, exclude_flagged=True).content.decode())
        )
    )
    assert len(full) == 1
    assert len(excluded) == 0
