"""Optional participant accounts: a stable participant code as identity."""
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

pytestmark = pytest.mark.django_db


def _study(slug, **kw):
    exp = ExperimentFactory(
        slug=slug, require_audio_check=False, collect_participant_code=True, **kw
    )
    cond = ConditionFactory(experiment=exp, name="C")
    TextStimulusFactory(condition=cond, title="t", sort_order=0)
    RatingQuestionFactory(experiment=exp, sort_order=0)
    exp.state = Experiment.State.ACTIVE
    exp.save(update_fields=["state"])
    return exp


def _consent(exp):
    return reverse("survey:consent", kwargs={"slug": exp.slug})


def _complete_with_code(client, exp, code):
    client.post(_consent(exp), {"agree": "on", "participant_code": code})
    client.post(reverse("survey:instructions", kwargs={"slug": exp.slug}))
    play = reverse("survey:play", kwargs={"slug": exp.slug})
    body = client.get(play).content.decode()
    (qid,) = {int(x) for x in re.findall(r'name="q_(\d+)', body)}
    client.post(play, {f"q_{qid}": "50"}, follow=True)


def test_code_input_shown_on_consent():
    exp = _study("pc0")
    assert 'name="participant_code"' in Client().get(_consent(exp)).content.decode()


def test_participant_code_used_as_uid():
    exp = _study("pc1")
    Client().post(_consent(exp), {"agree": "on", "participant_code": "P-42"})
    assert ParticipantSession.objects.get().participant_uid == "P-42"


def test_missing_code_blocks_start():
    exp = _study("pc2")
    resp = Client().post(_consent(exp), {"agree": "on"})
    assert resp.status_code == 200
    assert ParticipantSession.objects.count() == 0


def test_cross_device_duplicate_blocked_by_code():
    exp = _study("pc3", one_submission_per_participant=True)
    _complete_with_code(Client(), exp, "P-1")
    # A different browser using the same code is recognised and blocked.
    resp = Client().post(_consent(exp), {"agree": "on", "participant_code": "P-1"})
    assert b"already completed" in resp.content.lower()
    assert ParticipantSession.objects.filter(submitted_at__isnull=False).count() == 1
