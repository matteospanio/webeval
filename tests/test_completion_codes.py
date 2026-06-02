"""Completion codes, external-id capture, and compensation reconciliation."""
from __future__ import annotations

import csv as csvmod
import io
import re

import pytest
from django.test import Client
from django.urls import reverse

from experiments.csv_exports import completion_codes_csv_response
from experiments.models import Experiment
from experiments.tests.factories import (
    ConditionFactory,
    ExperimentFactory,
    RatingQuestionFactory,
    TextStimulusFactory,
)
from survey.models import ParticipantSession

pytestmark = pytest.mark.django_db


def _study(slug, **kw):
    exp = ExperimentFactory(slug=slug, require_audio_check=False, **kw)
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
    return client.post(play, {f"q_{qid}": "50"}, follow=True)


def test_unique_completion_code_shown_on_thanks():
    exp = _study("cc1", completion_code_mode=Experiment.CompletionCodeMode.UNIQUE)
    resp = _complete(Client(), exp)
    session = ParticipantSession.objects.get()
    assert session.completion_code
    assert session.completion_code.encode() in resp.content


def test_fixed_completion_code():
    exp = _study(
        "cc2",
        completion_code_mode=Experiment.CompletionCodeMode.FIXED,
        completion_code="THANKS-123",
    )
    resp = _complete(Client(), exp)
    assert ParticipantSession.objects.get().completion_code == "THANKS-123"
    assert b"THANKS-123" in resp.content


def test_no_completion_code_when_mode_none():
    exp = _study("cc3")  # default mode = none
    _complete(Client(), exp)
    assert ParticipantSession.objects.get().completion_code == ""


def test_external_id_captured_from_query_param():
    exp = _study("cc4", external_id_param="PROLIFIC_PID")
    client = Client()
    consent_url = reverse("survey:consent", kwargs={"slug": exp.slug})
    client.get(consent_url + "?PROLIFIC_PID=worker-999")  # land via the platform link
    _complete(client, exp)
    assert ParticipantSession.objects.get().external_id == "worker-999"


def test_completion_codes_csv_for_reconciliation():
    exp = _study(
        "cc5",
        completion_code_mode=Experiment.CompletionCodeMode.UNIQUE,
        external_id_param="pid",
    )
    client = Client()
    client.get(reverse("survey:consent", kwargs={"slug": exp.slug}) + "?pid=abc")
    _complete(client, exp)
    rows = list(
        csvmod.DictReader(
            io.StringIO(completion_codes_csv_response(exp).content.decode())
        )
    )
    assert len(rows) == 1
    assert rows[0]["external_id"] == "abc"
    assert rows[0]["completion_code"]
    assert rows[0]["compensation_status"] == "pending"
