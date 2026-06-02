"""Page/question response-time capture (Epic 6)."""
from __future__ import annotations

import re

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from experiments.analysis import analyse_question
from experiments.models import Experiment
from experiments.tests.factories import (
    ConditionFactory,
    ExperimentFactory,
    RatingQuestionFactory,
    TextStimulusFactory,
)
from survey.models import Response

pytestmark = pytest.mark.django_db


def _now_ms():
    return int(timezone.now().timestamp() * 1000)


def _study(slug):
    exp = ExperimentFactory(slug=slug, require_audio_check=False)
    cond = ConditionFactory(experiment=exp, name="C")
    TextStimulusFactory(condition=cond, title="t", sort_order=0)
    q = RatingQuestionFactory(experiment=exp, sort_order=0)
    exp.state = Experiment.State.ACTIVE
    exp.save(update_fields=["state"])
    return exp, q


def _walk_to_play(client, exp):
    client.post(reverse("survey:consent", kwargs={"slug": exp.slug}), {"agree": "on"})
    client.post(reverse("survey:instructions", kwargs={"slug": exp.slug}))
    return reverse("survey:play", kwargs={"slug": exp.slug})


def test_play_page_serves_timing_field():
    exp, q = _study("t1")
    client = Client()
    play = _walk_to_play(client, exp)
    assert 'name="_t0"' in client.get(play).content.decode()


def test_response_records_elapsed_ms():
    exp, q = _study("t2")
    client = Client()
    play = _walk_to_play(client, exp)
    body = client.get(play).content.decode()
    (qid,) = {int(x) for x in re.findall(r'name="q_(\d+)', body)}

    t0 = _now_ms() - 1500  # pretend the page was shown 1.5s ago
    client.post(play, {f"q_{qid}": "50", "_t0": str(t0)}, follow=True)

    r = Response.objects.get(question_id=qid)
    assert r.elapsed_ms is not None
    assert 1000 < r.elapsed_ms < 10000
    # The analysis surfaces a median page time for the question.
    assert analyse_question(exp, q).median_time_ms == r.elapsed_ms


def test_absurd_timing_is_dropped():
    exp, q = _study("t3")
    client = Client()
    play = _walk_to_play(client, exp)
    body = client.get(play).content.decode()
    (qid,) = {int(x) for x in re.findall(r'name="q_(\d+)', body)}

    # A _t0 far in the future yields a negative delta → not stored.
    client.post(play, {f"q_{qid}": "50", "_t0": str(_now_ms() + 999999)}, follow=True)
    assert Response.objects.get(question_id=qid).elapsed_ms is None
