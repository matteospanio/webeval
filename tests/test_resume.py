"""Save & resume: a session can be re-entered from a secret token link."""
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


def _active_experiment(slug):
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


def test_session_gets_resume_token_on_creation():
    exp = _active_experiment("res1")
    Client().post(reverse("survey:consent", kwargs={"slug": exp.slug}), {"agree": "on"})
    assert ParticipantSession.objects.get().resume_token


def test_resume_link_appears_on_play_page():
    exp = _active_experiment("res2")
    client = Client()
    _start(client, exp)
    body = client.get(reverse("survey:play", kwargs={"slug": exp.slug})).content.decode()
    session = ParticipantSession.objects.get()
    assert session.resume_token in body
    assert "Save &amp; continue later" in body


def test_resume_on_fresh_client_reestablishes_session():
    exp = _active_experiment("res3")
    c1 = Client()
    _start(c1, exp)
    session = ParticipantSession.objects.get()
    assert session.last_step == ParticipantSession.Step.STIMULI

    # A different browser (no cookie) opens the resume link.
    c2 = Client()
    resume_url = reverse(
        "survey:resume", kwargs={"slug": exp.slug, "token": session.resume_token}
    )
    resp = c2.get(resume_url)
    assert resp.status_code == 302
    assert resp.url == reverse("survey:play", kwargs={"slug": exp.slug})
    # The cookie is now established → the play page loads for c2.
    assert c2.get(reverse("survey:play", kwargs={"slug": exp.slug})).status_code == 200


def test_resume_of_completed_session_redirects_to_thanks():
    exp = _active_experiment("res4")
    c1 = Client()
    _start(c1, exp)
    play = reverse("survey:play", kwargs={"slug": exp.slug})
    body = c1.get(play).content.decode()
    (qid,) = {int(x) for x in re.findall(r'name="q_(\d+)', body)}
    c1.post(play, {f"q_{qid}": "50"}, follow=True)

    session = ParticipantSession.objects.get()
    assert session.submitted_at is not None
    resume_url = reverse(
        "survey:resume", kwargs={"slug": exp.slug, "token": session.resume_token}
    )
    resp = Client().get(resume_url)
    assert resp.status_code == 302
    assert resp.url == reverse("survey:thanks", kwargs={"slug": exp.slug})


def test_invalid_resume_token_returns_404():
    exp = _active_experiment("res5")
    resp = Client().get(
        reverse("survey:resume", kwargs={"slug": exp.slug, "token": "not-a-real-token"})
    )
    assert resp.status_code == 404
