"""Tests for the pre-survey audio playback check step."""
from __future__ import annotations

import pytest
from django.test import Client
from django.urls import reverse

from experiments.models import Experiment
from experiments.tests.factories import (
    ConditionFactory,
    ExperimentFactory,
    ImageStimulusFactory,
    RatingQuestionFactory,
    StimulusFactory,
)
from survey.models import ParticipantSession

pytestmark = pytest.mark.django_db


def _active_audio_experiment(slug: str, *, require_audio_check: bool = True) -> Experiment:
    exp = ExperimentFactory(slug=slug, require_audio_check=require_audio_check)
    cond = ConditionFactory(experiment=exp, name="A")
    StimulusFactory(condition=cond, title="a1")
    RatingQuestionFactory(experiment=exp, prompt="Quality?", sort_order=0)
    exp.state = Experiment.State.ACTIVE
    exp.save(update_fields=["state"])
    return exp


def _active_image_experiment(slug: str) -> Experiment:
    exp = ExperimentFactory(slug=slug, require_audio_check=True)
    cond = ConditionFactory(experiment=exp, name="A")
    ImageStimulusFactory(condition=cond, title="img1")
    RatingQuestionFactory(experiment=exp, prompt="Quality?", sort_order=0)
    exp.state = Experiment.State.ACTIVE
    exp.save(update_fields=["state"])
    return exp


def _walk_to_audio_check(client: Client, exp: Experiment) -> None:
    client.post(reverse("survey:consent", kwargs={"slug": exp.slug}), data={"agree": "on"})


def test_consent_post_redirects_to_audio_check_for_audio_experiment():
    exp = _active_audio_experiment("ac-redir")
    client = Client()
    resp = client.post(
        reverse("survey:consent", kwargs={"slug": exp.slug}), data={"agree": "on"}
    )
    assert resp.status_code in (302, 303)
    assert resp["Location"].endswith("/audio-check/")
    session = ParticipantSession.objects.get()
    assert session.last_step == ParticipantSession.Step.AUDIO_CHECK


def test_confirmed_volume_advances_to_instructions():
    exp = _active_audio_experiment("ac-ok")
    client = Client()
    _walk_to_audio_check(client, exp)
    resp = client.post(
        reverse("survey:audio_check", kwargs={"slug": exp.slug}),
        data={"can_hear": "yes"},
    )
    assert resp.status_code in (302, 303)
    assert resp["Location"].endswith("/instructions/")
    session = ParticipantSession.objects.get()
    assert session.last_step == ParticipantSession.Step.INSTRUCTIONS


def test_missing_can_hear_rerender_with_400():
    exp = _active_audio_experiment("ac-noconfirm")
    client = Client()
    _walk_to_audio_check(client, exp)
    resp = client.post(
        reverse("survey:audio_check", kwargs={"slug": exp.slug}),
        data={},
    )
    assert resp.status_code == 400
    session = ParticipantSession.objects.get()
    assert session.last_step == ParticipantSession.Step.AUDIO_CHECK


def test_skipped_when_toggle_off():
    exp = _active_audio_experiment("ac-off", require_audio_check=False)
    client = Client()
    client.post(reverse("survey:consent", kwargs={"slug": exp.slug}), data={"agree": "on"})
    resp = client.post(reverse("survey:instructions", kwargs={"slug": exp.slug}))
    assert resp.status_code in (302, 303)
    assert resp["Location"].endswith("/play/")
    session = ParticipantSession.objects.get()
    assert session.last_step == ParticipantSession.Step.STIMULI


def test_skipped_when_no_audio_stimuli():
    exp = _active_image_experiment("ac-image-only")
    client = Client()
    client.post(reverse("survey:consent", kwargs={"slug": exp.slug}), data={"agree": "on"})
    resp = client.post(reverse("survey:instructions", kwargs={"slug": exp.slug}))
    assert resp.status_code in (302, 303)
    assert resp["Location"].endswith("/play/")
    session = ParticipantSession.objects.get()
    assert session.last_step == ParticipantSession.Step.STIMULI
