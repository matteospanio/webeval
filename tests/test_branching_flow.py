"""Participant-flow skip logic: cross-page show/hide and whole-page auto-skip."""
from __future__ import annotations

import pytest
from django.test import Client
from django.urls import reverse

from experiments.models import Experiment, Question
from experiments.tests.factories import (
    ChoiceQuestionFactory,
    ConditionFactory,
    ExperimentFactory,
    RatingQuestionFactory,
    TextQuestionFactory,
    TextStimulusFactory,
)
from survey.models import ParticipantSession, Response

pytestmark = pytest.mark.django_db


def _consent_and_instructions(client, exp):
    client.post(reverse("survey:consent", kwargs={"slug": exp.slug}), {"agree": "on"})
    # follow → play → (no/▶ stimuli) lands the session on its next step
    client.post(reverse("survey:instructions", kwargs={"slug": exp.slug}), follow=True)


# --- demographic-section branching (no stimuli) -----------------------------


def _demographic_branch_experiment(slug):
    exp = ExperimentFactory(slug=slug, require_audio_check=False)
    q1 = ChoiceQuestionFactory(
        experiment=exp,
        section=Question.Section.DEMOGRAPHIC,
        prompt="Do you play music?",
        sort_order=0,
        required=True,
        config={"choices": ["Yes", "No"], "multi": False},
    )
    q2 = TextQuestionFactory(
        experiment=exp,
        section=Question.Section.DEMOGRAPHIC,
        prompt="Which instrument?",
        sort_order=1,
        page_break_before=True,
        required=True,
        config={"max_length": 100},
        visible_if={"question": q1.pk, "op": "eq", "value": "Yes"},
    )
    exp.state = Experiment.State.ACTIVE
    exp.save(update_fields=["state"])
    return exp, q1, q2


def test_demographic_branch_skips_when_false():
    exp, q1, q2 = _demographic_branch_experiment("branch-false")
    client = Client()
    _consent_and_instructions(client, exp)
    demo = reverse("survey:demographics", kwargs={"slug": exp.slug})
    assert "Do you play music?" in client.get(demo).content.decode()

    # Answer "No" → the dependent page is auto-skipped and the survey finishes.
    client.post(demo, {f"q_{q1.pk}": "No"}, follow=True)

    session = ParticipantSession.objects.get()
    assert session.last_step == ParticipantSession.Step.DONE
    assert Response.objects.filter(question=q1).exists()
    assert not Response.objects.filter(question=q2).exists()


def test_demographic_branch_shows_when_true():
    exp, q1, q2 = _demographic_branch_experiment("branch-true")
    client = Client()
    _consent_and_instructions(client, exp)
    demo = reverse("survey:demographics", kwargs={"slug": exp.slug})

    client.post(demo, {f"q_{q1.pk}": "Yes"})
    body = client.get(demo).content.decode()
    assert "Which instrument?" in body  # dependent now visible on its own page

    client.post(demo, {f"q_{q2.pk}": "guitar"}, follow=True)
    session = ParticipantSession.objects.get()
    assert session.last_step == ParticipantSession.Step.DONE
    assert Response.objects.get(question=q2).get_answer() == "guitar"


# --- stimulus-section branching ---------------------------------------------


def test_stimulus_branch_skips_hidden_question():
    exp = ExperimentFactory(slug="sbranch", require_audio_check=False)
    cond = ConditionFactory(experiment=exp, name="C")
    TextStimulusFactory(condition=cond, title="t", sort_order=0)
    q1 = ChoiceQuestionFactory(
        experiment=exp,
        section=Question.Section.STIMULUS,
        prompt="Was it audible?",
        sort_order=0,
        required=True,
        config={"choices": ["Yes", "No"], "multi": False},
    )
    q2 = RatingQuestionFactory(
        experiment=exp,
        section=Question.Section.STIMULUS,
        prompt="Rate the quality",
        sort_order=1,
        page_break_before=True,
        required=True,
        visible_if={"question": q1.pk, "op": "eq", "value": "Yes"},
    )
    exp.state = Experiment.State.ACTIVE
    exp.save(update_fields=["state"])

    client = Client()
    client.post(reverse("survey:consent", kwargs={"slug": exp.slug}), {"agree": "on"})
    client.post(reverse("survey:instructions", kwargs={"slug": exp.slug}))
    play = reverse("survey:play", kwargs={"slug": exp.slug})
    # Branching disables question shuffle, so q1 is deterministically first.
    assert "Was it audible?" in client.get(play).content.decode()

    client.post(play, {f"q_{q1.pk}": "No"}, follow=True)
    session = ParticipantSession.objects.get()
    assert session.last_step == ParticipantSession.Step.DONE
    assert Response.objects.filter(question=q1).exists()
    assert not Response.objects.filter(question=q2).exists()


def test_stimulus_branch_shows_and_requires_when_true():
    exp = ExperimentFactory(slug="sbranch2", require_audio_check=False)
    cond = ConditionFactory(experiment=exp, name="C")
    TextStimulusFactory(condition=cond, title="t", sort_order=0)
    q1 = ChoiceQuestionFactory(
        experiment=exp,
        section=Question.Section.STIMULUS,
        prompt="Was it audible?",
        sort_order=0,
        required=True,
        config={"choices": ["Yes", "No"], "multi": False},
    )
    q2 = RatingQuestionFactory(
        experiment=exp,
        section=Question.Section.STIMULUS,
        prompt="Rate the quality",
        sort_order=1,
        page_break_before=True,
        required=True,
        visible_if={"question": q1.pk, "op": "eq", "value": "Yes"},
    )
    exp.state = Experiment.State.ACTIVE
    exp.save(update_fields=["state"])

    client = Client()
    client.post(reverse("survey:consent", kwargs={"slug": exp.slug}), {"agree": "on"})
    client.post(reverse("survey:instructions", kwargs={"slug": exp.slug}))
    play = reverse("survey:play", kwargs={"slug": exp.slug})
    client.post(play, {f"q_{q1.pk}": "Yes"})
    assert "Rate the quality" in client.get(play).content.decode()
    client.post(play, {f"q_{q2.pk}": "80"}, follow=True)

    session = ParticipantSession.objects.get()
    assert session.last_step == ParticipantSession.Step.DONE
    assert Response.objects.get(question=q2).get_answer() == 80
