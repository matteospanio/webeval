"""Raw participant-flow event logging (Epic 6)."""
from __future__ import annotations

import re

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from accounts.tests.factories import UserFactory
from experiments.models import Experiment
from experiments.tests.factories import (
    ConditionFactory,
    ExperimentFactory,
    RatingQuestionFactory,
    TextStimulusFactory,
)
from survey.models import ParticipantSession, SurveyEvent
from survey.views import _withdraw_data

pytestmark = pytest.mark.django_db


def _study(slug, owner=None):
    exp = ExperimentFactory(slug=slug, owner=owner, require_audio_check=False)
    cond = ConditionFactory(experiment=exp, name="C")
    TextStimulusFactory(condition=cond, title="t", sort_order=0)
    RatingQuestionFactory(experiment=exp, sort_order=0)
    exp.state = Experiment.State.ACTIVE
    exp.save(update_fields=["state"])
    return exp


def _complete(client, exp):
    client.post(reverse("survey:consent", kwargs={"slug": exp.slug}), {"agree": "on"})
    client.post(reverse("survey:instructions", kwargs={"slug": exp.slug}))
    play = reverse("survey:play", kwargs={"slug": exp.slug})
    body = client.get(play).content.decode()
    (qid,) = {int(x) for x in re.findall(r'name="q_(\d+)', body)}
    client.post(play, {f"q_{qid}": "50"}, follow=True)


def test_flow_logs_started_pagesubmit_completed():
    exp = _study("ev1")
    _complete(Client(), exp)
    session = ParticipantSession.objects.get(experiment=exp)
    types = list(session.events.values_list("event_type", flat=True))
    assert "started" in types
    assert "page_submit" in types
    assert "completed" in types


def test_withdrawal_erases_events():
    exp = _study("ev2")
    session = ParticipantSession.objects.create(
        experiment=exp, consented_at=timezone.now(), resume_token="tok"
    )
    SurveyEvent.objects.create(session=session, event_type=SurveyEvent.Type.STARTED)
    _withdraw_data(session)
    assert not SurveyEvent.objects.filter(session=session).exists()


def test_events_csv_export():
    owner = UserFactory()
    exp = _study("ev3", owner=owner)
    _complete(Client(), exp)  # participant (anonymous)

    staff = Client()
    staff.force_login(owner)
    body = staff.get(
        reverse("studio:events_csv", kwargs={"slug": exp.slug})
    ).content.decode()
    assert "event_type" in body  # header
    assert "started" in body and "completed" in body
