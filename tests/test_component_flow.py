"""End-to-end participant flow for a plugin question-type component."""
from __future__ import annotations

import re

import pytest
from django.test import Client
from django.urls import reverse

from experiments.models import Experiment, Question
from experiments.tests.factories import (
    ConditionFactory,
    ExperimentFactory,
    TextStimulusFactory,
)
from survey.models import ParticipantSession, Response

pytestmark = pytest.mark.django_db


def _study_with_constant_sum(slug):
    exp = ExperimentFactory(slug=slug, require_audio_check=False)
    cond = ConditionFactory(experiment=exp, name="C")
    TextStimulusFactory(condition=cond, title="t", sort_order=0)
    q = Question(
        experiment=exp,
        section=Question.Section.STIMULUS,
        type="constant_sum",
        prompt="Split your budget",
        sort_order=0,
        config={"items": ["Rent", "Food"], "total": 100},
    )
    q.save()
    exp.state = Experiment.State.ACTIVE
    exp.save(update_fields=["state"])
    return exp, q


def _walk_to_play(client, exp):
    client.post(reverse("survey:consent", kwargs={"slug": exp.slug}), {"agree": "on"})
    client.post(reverse("survey:instructions", kwargs={"slug": exp.slug}))
    return reverse("survey:play", kwargs={"slug": exp.slug})


def test_plugin_widget_renders_and_answer_is_stored():
    exp, q = _study_with_constant_sum("cs1")
    client = Client()
    play = _walk_to_play(client, exp)

    body = client.get(play).content.decode()
    assert f'name="q_{q.pk}_i0"' in body
    assert f'name="q_{q.pk}_i1"' in body
    assert "Distribute exactly 100 points" in body

    client.post(play, {f"q_{q.pk}_i0": "60", f"q_{q.pk}_i1": "40"}, follow=True)
    session = ParticipantSession.objects.get(experiment=exp)
    assert session.submitted_at is not None
    answer = Response.objects.get(session=session, question=q).get_answer()
    assert answer == {"Rent": 60, "Food": 40}


def test_plugin_widget_rejects_wrong_total_and_repopulates():
    exp, q = _study_with_constant_sum("cs2")
    client = Client()
    play = _walk_to_play(client, exp)

    resp = client.post(play, {f"q_{q.pk}_i0": "60", f"q_{q.pk}_i1": "30"})  # sums to 90
    assert resp.status_code == 400  # validation errors re-render with 400
    body = resp.content.decode()
    assert "must add up to 100" in body
    assert 'value="60"' in body  # the participant's entries are preserved
    assert not Response.objects.filter(question=q).exists()


def test_required_plugin_question_blocks_empty_submit():
    exp, q = _study_with_constant_sum("cs3")
    client = Client()
    play = _walk_to_play(client, exp)

    resp = client.post(play, {})  # nothing entered
    assert resp.status_code == 400  # validation errors re-render with 400
    assert re.search(r"required", resp.content.decode(), re.IGNORECASE)
    assert not ParticipantSession.objects.filter(
        experiment=exp, submitted_at__isnull=False
    ).exists()
