"""End-to-end test for PAIRWISE_AUDIO mode.

Builds a small audio-continuation experiment, walks a participant through
the pairwise comparison pages, verifies the prompt audio is rendered above
both continuations, and checks that the record_listen_pair endpoint now
accepts side="prompt".
"""
from __future__ import annotations

import json

import pytest
from django.test import Client
from django.urls import reverse

from experiments.models import Experiment, Question
from experiments.tests.factories import (
    ChoiceQuestionFactory,
    ConditionFactory,
    PairwiseAudioExperimentFactory,
    PromptFactory,
    StimulusFactory,
)
from survey.models import PairAssignment, ParticipantSession

pytestmark = pytest.mark.django_db


def _build_pairwise_audio_experiment():
    exp = PairwiseAudioExperimentFactory(
        slug="pa-e2e",
        name="Pairwise Audio E2E",
        stimuli_per_participant=1,
        require_audio_check=False,
    )
    cond_a = ConditionFactory(experiment=exp, name="Model-A")
    cond_b = ConditionFactory(experiment=exp, name="Model-B")
    StimulusFactory(condition=cond_a, prompt_group="song-01", title="a-cont")
    StimulusFactory(condition=cond_b, prompt_group="song-01", title="b-cont")
    PromptFactory(
        experiment=exp,
        prompt_group="song-01",
        title="Song 01 intro",
        description="First 4 measures",
    )
    ChoiceQuestionFactory(
        experiment=exp,
        section=Question.Section.STIMULUS,
        prompt="Which continuation is more musical?",
        config={"choices": ["A", "B"], "multi": False},
        required=True,
    )
    exp.state = Experiment.State.ACTIVE
    exp.save(update_fields=["state"])
    return exp


def test_pairwise_audio_flow_renders_prompt_and_completes():
    exp = _build_pairwise_audio_experiment()
    client = Client()

    client.post(
        reverse("survey:consent", kwargs={"slug": exp.slug}),
        data={"agree": "on"},
    )
    client.post(reverse("survey:instructions", kwargs={"slug": exp.slug}))

    compare_url = reverse("survey:pairwise_play", kwargs={"slug": exp.slug})
    page = client.get(compare_url)
    assert page.status_code == 200
    body = page.content.decode()

    # The prompt audio player must appear alongside the two continuation players.
    assert 'id="stimulus-audio-prompt"' in body
    assert 'id="stimulus-audio-left"' in body
    assert 'id="stimulus-audio-right"' in body
    assert 'data-listen-side="prompt"' in body

    # Find the stimulus question and answer it.
    import re
    qids = {int(x) for x in re.findall(r'name="q_(\d+)"', body)}
    assert qids
    (qid,) = qids
    resp = client.post(compare_url, data={f"q_{qid}": "A"})
    assert resp.status_code in (302, 303)

    session = ParticipantSession.objects.get()
    assert session.last_step == ParticipantSession.Step.DEMOGRAPHICS
    assert session.pair_assignments.count() == 1


def test_record_listen_pair_accepts_prompt_side():
    exp = _build_pairwise_audio_experiment()
    client = Client()

    client.post(
        reverse("survey:consent", kwargs={"slug": exp.slug}),
        data={"agree": "on"},
    )
    client.post(reverse("survey:instructions", kwargs={"slug": exp.slug}))

    session = ParticipantSession.objects.get()
    pair = session.pair_assignments.first()
    assert pair is not None

    listen_url = reverse(
        "survey:record_listen_pair",
        kwargs={"slug": exp.slug, "pair_id": pair.pk},
    )
    resp = client.post(
        listen_url,
        data=json.dumps({"duration_ms": 4200, "side": "prompt"}),
        content_type="application/json",
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}

    pair.refresh_from_db()
    assert pair.listen_duration_prompt_ms == 4200

    # Unknown side must fail.
    bad = client.post(
        listen_url,
        data=json.dumps({"duration_ms": 100, "side": "center"}),
        content_type="application/json",
    )
    assert bad.status_code == 400


def test_pairwise_audio_selects_pairs_sharing_prompt_group():
    """The existing pairwise_balanced strategy must pick across conditions for
    each shared prompt_group, unchanged in PAIRWISE_AUDIO mode."""
    exp = _build_pairwise_audio_experiment()
    client = Client()

    client.post(
        reverse("survey:consent", kwargs={"slug": exp.slug}),
        data={"agree": "on"},
    )
    client.post(reverse("survey:instructions", kwargs={"slug": exp.slug}))

    session = ParticipantSession.objects.get()
    pa_qs = PairAssignment.objects.filter(session=session)
    assert pa_qs.count() == 1
    pa = pa_qs.first()
    assert pa.prompt_group == "song-01"
    assert pa.stimulus_a.condition.name != pa.stimulus_b.condition.name
