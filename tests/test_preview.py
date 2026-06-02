"""Preview / pilot separation: TEST-phase sessions stay out of real results."""
from __future__ import annotations

import re

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from experiments.csv_exports import answers_csv_response
from experiments.models import Experiment
from experiments.stats import experiment_counts, per_stimulus_mean_ratings
from experiments.tests.factories import (
    ConditionFactory,
    ExperimentFactory,
    RatingQuestionFactory,
    TextStimulusFactory,
)
from survey.models import ParticipantSession

pytestmark = pytest.mark.django_db


def _study(slug, state):
    exp = ExperimentFactory(slug=slug, require_audio_check=False)
    cond = ConditionFactory(experiment=exp, name="C")
    TextStimulusFactory(condition=cond, title="t", sort_order=0)
    RatingQuestionFactory(experiment=exp, sort_order=0)
    exp.state = state
    exp.save(update_fields=["state"])
    return exp


def _complete(client, exp):
    client.post(reverse("survey:consent", kwargs={"slug": exp.slug}), {"agree": "on"})
    client.post(reverse("survey:instructions", kwargs={"slug": exp.slug}))
    play = reverse("survey:play", kwargs={"slug": exp.slug})
    body = client.get(play).content.decode()
    (qid,) = {int(x) for x in re.findall(r'name="q_(\d+)', body)}
    return client.post(play, {f"q_{qid}": "50"}, follow=True)


def test_test_phase_session_is_preview_and_excluded():
    exp = _study("prev1", Experiment.State.TEST)
    _complete(Client(), exp)
    session = ParticipantSession.objects.get(experiment=exp)
    assert session.is_preview is True
    assert session.submitted_at is not None
    # Kept out of the real dataset.
    assert experiment_counts(exp).completed_sessions == 0
    assert per_stimulus_mean_ratings(exp) == []
    rows = answers_csv_response(exp).content.decode().strip().splitlines()
    assert len(rows) == 1  # header only


def test_active_session_counts_as_real():
    exp = _study("prev2", Experiment.State.ACTIVE)
    _complete(Client(), exp)
    session = ParticipantSession.objects.get(experiment=exp)
    assert session.is_preview is False
    assert experiment_counts(exp).completed_sessions == 1
    assert len(per_stimulus_mean_ratings(exp)) == 1


def test_keep_test_data_promotes_preview_sessions():
    exp = _study("prev3", Experiment.State.TEST)
    ParticipantSession.objects.create(
        experiment=exp,
        last_step=ParticipantSession.Step.DONE,
        consented_at=timezone.now(),
        submitted_at=timezone.now(),
        is_preview=True,
    )
    assert experiment_counts(exp).completed_sessions == 0  # preview is hidden

    user = User.objects.create_user(
        "prevadmin", "p@e.org", "pw", is_staff=True, is_superuser=True
    )
    client = Client()
    client.force_login(user)
    url = reverse("admin:experiments_experiment_activate", kwargs={"slug": exp.slug})
    resp = client.post(url, {})  # purge unchecked → keep test data
    assert resp.status_code == 302
    exp.refresh_from_db()
    assert exp.state == Experiment.State.ACTIVE
    assert ParticipantSession.objects.get(experiment=exp).is_preview is False
    assert experiment_counts(exp).completed_sessions == 1  # promoted, now counted
