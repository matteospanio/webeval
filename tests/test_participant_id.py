"""Stable participant IDs + duplicate-submission controls."""
from __future__ import annotations

import re

import pytest
from django.test import Client
from django.urls import reverse

from experiments.models import Experiment
from experiments.tests.factories import (
    ConditionFactory,
    ExperimentFactory,
    RatingQuestionFactory,
    TextStimulusFactory,
)
from survey.models import ParticipantSession
from survey.views import PARTICIPANT_COOKIE

pytestmark = pytest.mark.django_db


def _study(slug, one_per=False):
    exp = ExperimentFactory(
        slug=slug,
        require_audio_check=False,
        one_submission_per_participant=one_per,
    )
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


def test_consent_sets_participant_cookie_and_stores_uid():
    exp = _study("pid1")
    client = Client()
    resp = client.get(reverse("survey:consent", kwargs={"slug": exp.slug}))
    assert PARTICIPANT_COOKIE in resp.cookies
    pid = resp.cookies[PARTICIPANT_COOKIE].value
    client.post(reverse("survey:consent", kwargs={"slug": exp.slug}), {"agree": "on"})
    assert ParticipantSession.objects.get().participant_uid == pid


def test_duplicate_completion_is_flagged():
    exp = _study("pid2")
    client = Client()
    _complete(client, exp)  # first completion
    _complete(client, exp)  # second completion, same cookie

    sessions = ParticipantSession.objects.filter(
        submitted_at__isnull=False
    ).order_by("started_at")
    assert sessions.count() == 2
    assert "duplicate" in sessions.last().flags


def test_one_submission_per_participant_blocks_second_start():
    exp = _study("pid3", one_per=True)
    client = Client()
    _complete(client, exp)

    resp = client.get(reverse("survey:consent", kwargs={"slug": exp.slug}))
    assert resp.status_code == 200
    assert b"already completed" in resp.content.lower()
    assert ParticipantSession.objects.filter(submitted_at__isnull=False).count() == 1
