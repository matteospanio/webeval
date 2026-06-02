"""Participant-visible withdrawal + data deletion."""
from __future__ import annotations

import csv as csvmod
import io
import re

import pytest
from django.test import Client
from django.urls import reverse

from experiments.csv_exports import answers_csv_response
from experiments.models import Experiment
from experiments.tests.factories import (
    ConditionFactory,
    ExperimentFactory,
    RatingQuestionFactory,
    TextStimulusFactory,
)
from survey.models import ParticipantSession, Response

pytestmark = pytest.mark.django_db


def _study(slug):
    exp = ExperimentFactory(slug=slug, require_audio_check=False)
    cond = ConditionFactory(experiment=exp, name="C")
    TextStimulusFactory(condition=cond, title="t", sort_order=0)
    RatingQuestionFactory(experiment=exp, sort_order=0)
    exp.state = Experiment.State.ACTIVE
    exp.save(update_fields=["state"])
    return exp


def _start(client, exp):
    client.post(reverse("survey:consent", kwargs={"slug": exp.slug}), {"agree": "on"})
    client.post(reverse("survey:instructions", kwargs={"slug": exp.slug}))


def test_withdraw_link_shown_during_survey():
    exp = _study("w1")
    client = Client()
    _start(client, exp)
    body = client.get(reverse("survey:play", kwargs={"slug": exp.slug})).content.decode()
    session = ParticipantSession.objects.get()
    assert (
        reverse("survey:withdraw", kwargs={"slug": exp.slug, "token": session.resume_token})
        in body
    )


def test_withdraw_deletes_responses_and_anonymises():
    exp = _study("w2")
    client = Client()
    _start(client, exp)
    play = reverse("survey:play", kwargs={"slug": exp.slug})
    body = client.get(play).content.decode()
    (qid,) = {int(x) for x in re.findall(r'name="q_(\d+)', body)}
    client.post(play, {f"q_{qid}": "50"})  # answer one stimulus (mid-flow)

    session = ParticipantSession.objects.get()
    assert Response.objects.filter(session=session).exists()
    token = session.resume_token
    withdraw_url = reverse("survey:withdraw", kwargs={"slug": exp.slug, "token": token})

    assert b"Withdraw" in client.get(withdraw_url).content
    resp = client.post(withdraw_url)
    assert resp.status_code == 200
    assert b"removed" in resp.content.lower()

    session.refresh_from_db()
    assert session.withdrawn_at is not None
    assert session.last_step == ParticipantSession.Step.WITHDRAWN
    assert session.resume_token is None
    assert session.participant_uid == ""
    assert not Response.objects.filter(session=session).exists()
    assert not session.assignments.exists()

    # The now-dead token no longer resolves to a session.
    assert Client().get(withdraw_url).status_code == 200


def test_withdrawn_session_excluded_from_answers_csv():
    exp = _study("w3")
    client = Client()
    _start(client, exp)
    play = reverse("survey:play", kwargs={"slug": exp.slug})
    body = client.get(play).content.decode()
    (qid,) = {int(x) for x in re.findall(r'name="q_(\d+)', body)}
    client.post(play, {f"q_{qid}": "50"}, follow=True)  # complete

    session = ParticipantSession.objects.get()
    assert session.submitted_at is not None
    token = session.resume_token

    Client().post(reverse("survey:withdraw", kwargs={"slug": exp.slug, "token": token}))
    rows = list(
        csvmod.DictReader(io.StringIO(answers_csv_response(exp).content.decode()))
    )
    assert rows == []
