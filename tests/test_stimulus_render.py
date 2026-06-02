"""Participant-flow rendering of the video / html / embed stimulus kinds."""
from __future__ import annotations

import re

import pytest
from django.test import Client
from django.urls import reverse

from experiments.models import Experiment
from experiments.tests.factories import (
    ConditionFactory,
    EmbedStimulusFactory,
    ExperimentFactory,
    HtmlStimulusFactory,
    RatingQuestionFactory,
    VideoStimulusFactory,
)

pytestmark = pytest.mark.django_db


def test_play_renders_video_html_and_embed():
    exp = ExperimentFactory(slug="mm", name="MM", require_audio_check=False)
    cond = ConditionFactory(experiment=exp, name="C")
    VideoStimulusFactory(condition=cond, title="vid", sort_order=0)
    HtmlStimulusFactory(condition=cond, title="htm", sort_order=1)
    EmbedStimulusFactory(condition=cond, title="emb", sort_order=2)
    RatingQuestionFactory(experiment=exp, sort_order=0)
    exp.state = Experiment.State.ACTIVE
    exp.save(update_fields=["state"])

    client = Client()
    client.post(
        reverse("survey:consent", kwargs={"slug": exp.slug}), data={"agree": "on"}
    )
    client.post(reverse("survey:instructions", kwargs={"slug": exp.slug}))

    play_url = reverse("survey:play", kwargs={"slug": exp.slug})
    bodies = []
    for _ in range(3):
        body = client.get(play_url).content.decode()
        bodies.append(body)
        (qid,) = {int(x) for x in re.findall(r'name="q_(\d+)', body)}
        client.post(play_url, data={f"q_{qid}": "50"})
    joined = "\n".join(bodies)

    assert "<video" in joined
    assert "data-listen-endpoint" in joined  # video opts into watch-time tracking
    assert "Hello <strong>world</strong>" in joined  # raw HTML rendered as-is
    assert "<iframe" in joined
    assert "example.org/embed/abc" in joined
