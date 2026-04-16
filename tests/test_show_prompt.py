"""Tests that stimulus generation prompt is shown/hidden based on Question.show_prompt."""
from __future__ import annotations

import pytest
from django.test import Client
from django.urls import reverse

from experiments.models import Experiment, Question
from experiments.tests.factories import (
    ConditionFactory,
    ExperimentFactory,
    RatingQuestionFactory,
    StimulusFactory,
)

pytestmark = pytest.mark.django_db

PROMPT_TEXT = "Describe a sunset over the ocean"


def _setup(*, show_prompt: bool):
    exp = ExperimentFactory(slug="prompt-test", require_audio_check=False)
    cond = ConditionFactory(experiment=exp)
    StimulusFactory(condition=cond, title="clip", sort_order=0, prompt_group=PROMPT_TEXT)
    RatingQuestionFactory(
        experiment=exp,
        prompt="Quality?",
        sort_order=0,
        show_prompt=show_prompt,
        config={"min": 0, "max": 100, "step": 1},
    )
    exp.state = Experiment.State.ACTIVE
    exp.save(update_fields=["state"])
    return exp


def _start_session(client, exp):
    client.post(
        reverse("survey:consent", kwargs={"slug": exp.slug}), data={"agree": "on"}
    )
    client.post(reverse("survey:instructions", kwargs={"slug": exp.slug}))


def test_prompt_shown_when_show_prompt_true():
    exp = _setup(show_prompt=True)
    client = Client()
    _start_session(client, exp)
    resp = client.get(reverse("survey:play", kwargs={"slug": exp.slug}))
    assert resp.status_code == 200
    body = resp.content.decode()
    assert PROMPT_TEXT in body
    assert "stimulus-prompt" in body


def test_prompt_hidden_when_show_prompt_false():
    exp = _setup(show_prompt=False)
    client = Client()
    _start_session(client, exp)
    resp = client.get(reverse("survey:play", kwargs={"slug": exp.slug}))
    assert resp.status_code == 200
    body = resp.content.decode()
    assert PROMPT_TEXT not in body
    assert "stimulus-prompt" not in body
