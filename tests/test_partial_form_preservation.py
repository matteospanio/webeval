"""Validation-error re-render preserves previously-entered answers."""
from __future__ import annotations

import re

import pytest
from django.test import Client
from django.urls import reverse

from experiments.models import Experiment, Question
from experiments.tests.factories import (
    ChoiceQuestionFactory,
    ConditionFactory,
    ExperimentFactory,
    RatingQuestionFactory,
    StimulusFactory,
    TextQuestionFactory,
)

pytestmark = pytest.mark.django_db


def _start_session(client, exp):
    client.post(
        reverse("survey:consent", kwargs={"slug": exp.slug}), data={"agree": "on"}
    )
    client.post(reverse("survey:instructions", kwargs={"slug": exp.slug}))


def test_stimulus_page_retains_rating_after_validation_error():
    exp = ExperimentFactory(slug="partial-rating", require_audio_check=False)
    cond = ConditionFactory(experiment=exp)
    StimulusFactory(condition=cond, title="clip", sort_order=0)
    rating = RatingQuestionFactory(
        experiment=exp, prompt="Quality?", sort_order=0,
        config={"min": 0, "max": 100, "step": 1},
    )
    choice = ChoiceQuestionFactory(
        experiment=exp, prompt="Genre?", sort_order=1,
        section=Question.Section.STIMULUS,
        required=True,
        config={"choices": ["rock", "jazz"], "multi": False},
    )
    exp.state = Experiment.State.ACTIVE
    exp.save(update_fields=["state"])

    client = Client()
    _start_session(client, exp)

    resp = client.post(
        reverse("survey:play", kwargs={"slug": exp.slug}),
        data={f"q_{rating.pk}": "73"},
    )
    assert resp.status_code == 400
    body = resp.content.decode()
    # Rating range input reflects the submitted value, not the default min.
    m = re.search(rf'<input[^>]*name="q_{rating.pk}"[^>]*>', body)
    assert m is not None
    assert 'value="73"' in m.group(0)
    # The choice radios are re-rendered with no selection (nothing was posted).
    choice_inputs = re.findall(
        rf'<input[^>]*name="q_{choice.pk}"[^>]*>', body
    )
    assert choice_inputs
    assert all("checked" not in inp for inp in choice_inputs)


def test_stimulus_page_retains_choice_after_validation_error():
    exp = ExperimentFactory(slug="partial-choice", require_audio_check=False)
    cond = ConditionFactory(experiment=exp)
    StimulusFactory(condition=cond, title="clip", sort_order=0)
    # Optional rating so the only missing-required is a *different* rating we add below.
    rating = RatingQuestionFactory(
        experiment=exp, prompt="Optional?", sort_order=0, required=False,
        config={"min": 0, "max": 100, "step": 1},
    )
    choice = ChoiceQuestionFactory(
        experiment=exp, prompt="Genre?", sort_order=1,
        section=Question.Section.STIMULUS,
        required=True,
        config={"choices": ["rock", "jazz"], "multi": False},
    )
    missing = RatingQuestionFactory(
        experiment=exp, prompt="Required rating?", sort_order=2, required=True,
        config={"min": 0, "max": 10, "step": 1},
    )
    exp.state = Experiment.State.ACTIVE
    exp.save(update_fields=["state"])

    client = Client()
    _start_session(client, exp)

    resp = client.post(
        reverse("survey:play", kwargs={"slug": exp.slug}),
        data={f"q_{choice.pk}": "jazz"},
    )
    assert resp.status_code == 400
    body = resp.content.decode()
    jazz = re.search(
        rf'<input[^>]*name="q_{choice.pk}"[^>]*value="jazz"[^>]*>', body
    )
    rock = re.search(
        rf'<input[^>]*name="q_{choice.pk}"[^>]*value="rock"[^>]*>', body
    )
    assert jazz is not None and "checked" in jazz.group(0)
    assert rock is not None and "checked" not in rock.group(0)


def test_demographics_page_retains_text_after_validation_error():
    exp = ExperimentFactory(slug="partial-demo", require_audio_check=False)
    cond = ConditionFactory(experiment=exp)
    StimulusFactory(condition=cond, title="clip", sort_order=0)
    stim_q = RatingQuestionFactory(
        experiment=exp, prompt="Quality?", sort_order=0,
        config={"min": 0, "max": 100, "step": 1},
    )
    demo_a = TextQuestionFactory(
        experiment=exp, prompt="City?", sort_order=1,
        section=Question.Section.DEMOGRAPHIC, required=True,
    )
    demo_b = TextQuestionFactory(
        experiment=exp, prompt="Country?", sort_order=2,
        section=Question.Section.DEMOGRAPHIC, required=True,
    )
    exp.state = Experiment.State.ACTIVE
    exp.save(update_fields=["state"])

    client = Client()
    _start_session(client, exp)

    # Clear the stimulus page.
    play_url = reverse("survey:play", kwargs={"slug": exp.slug})
    client.post(play_url, data={f"q_{stim_q.pk}": "42"})

    demo_url = reverse("survey:demographics", kwargs={"slug": exp.slug})
    resp = client.post(demo_url, data={f"q_{demo_a.pk}": "Milan"})
    assert resp.status_code == 400
    body = resp.content.decode()
    m = re.search(rf'<input[^>]*name="q_{demo_a.pk}"[^>]*>', body)
    assert m is not None and 'value="Milan"' in m.group(0)
