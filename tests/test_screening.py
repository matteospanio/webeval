"""Screening / eligibility flow: route to screening, pass through, or screen out."""
from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError
from django.test import Client
from django.urls import reverse

from experiments.models import Experiment, Question
from experiments.tests.factories import (
    ChoiceQuestionFactory,
    ConditionFactory,
    ExperimentFactory,
    RatingQuestionFactory,
    ScreeningQuestionFactory,
    TextStimulusFactory,
)
from survey.models import ParticipantSession, Response

pytestmark = pytest.mark.django_db


def _screened_experiment(slug, with_rule=True):
    exp = ExperimentFactory(slug=slug, require_audio_check=False)
    cond = ConditionFactory(experiment=exp, name="C")
    TextStimulusFactory(condition=cond, title="t", sort_order=0)
    RatingQuestionFactory(experiment=exp, sort_order=0)  # the main-task question
    screen_q = ScreeningQuestionFactory(experiment=exp, prompt="Are you 18+?", sort_order=0)
    if with_rule:
        exp.eligibility_rule = {"question": screen_q.pk, "op": "eq", "value": "Yes"}
    exp.state = Experiment.State.ACTIVE
    exp.save(update_fields=["eligibility_rule", "state"])
    return exp, screen_q


def test_consent_routes_to_screening():
    exp, _ = _screened_experiment("scr1")
    client = Client()
    resp = client.post(reverse("survey:consent", kwargs={"slug": exp.slug}), {"agree": "on"})
    assert resp.status_code == 302
    assert resp.url == reverse("survey:screening", kwargs={"slug": exp.slug})
    assert ParticipantSession.objects.get().last_step == ParticipantSession.Step.SCREENING


def test_eligible_participant_passes_to_instructions():
    exp, screen_q = _screened_experiment("scr2")
    client = Client()
    client.post(reverse("survey:consent", kwargs={"slug": exp.slug}), {"agree": "on"})
    screening_url = reverse("survey:screening", kwargs={"slug": exp.slug})
    assert "Before we begin" in client.get(screening_url).content.decode()

    resp = client.post(screening_url, {f"q_{screen_q.pk}": "Yes"})
    assert resp.status_code == 302
    assert resp.url == reverse("survey:instructions", kwargs={"slug": exp.slug})
    session = ParticipantSession.objects.get()
    assert session.last_step == ParticipantSession.Step.INSTRUCTIONS
    assert Response.objects.get(question=screen_q).get_answer() == "Yes"


def test_ineligible_participant_is_screened_out():
    exp, screen_q = _screened_experiment("scr3")
    client = Client()
    client.post(reverse("survey:consent", kwargs={"slug": exp.slug}), {"agree": "on"})
    screening_url = reverse("survey:screening", kwargs={"slug": exp.slug})

    resp = client.post(screening_url, {f"q_{screen_q.pk}": "No"})
    assert resp.status_code == 302
    assert resp.url == reverse("survey:screened_out", kwargs={"slug": exp.slug})

    session = ParticipantSession.objects.get()
    assert session.last_step == ParticipantSession.Step.SCREENED_OUT
    assert session.screened_out_at is not None
    assert session.submitted_at is None  # not counted as a completed response

    # The screen-out page renders, and the participant can't reach the task
    # (their session cookie was cleared → play bounces to consent).
    assert client.get(reverse("survey:screened_out", kwargs={"slug": exp.slug})).status_code == 200
    assert client.get(reverse("survey:play", kwargs={"slug": exp.slug})).status_code == 302


def test_no_rule_lets_everyone_through():
    exp, screen_q = _screened_experiment("scr4", with_rule=False)
    client = Client()
    client.post(reverse("survey:consent", kwargs={"slug": exp.slug}), {"agree": "on"})
    resp = client.post(
        reverse("survey:screening", kwargs={"slug": exp.slug}), {f"q_{screen_q.pk}": "No"}
    )
    assert resp.url == reverse("survey:instructions", kwargs={"slug": exp.slug})


def test_eligibility_rule_must_reference_screening_question():
    exp = ExperimentFactory()
    demo_q = ChoiceQuestionFactory(
        experiment=exp,
        section=Question.Section.DEMOGRAPHIC,
        config={"choices": ["x", "y"], "multi": False},
    )
    exp.eligibility_rule = {"question": demo_q.pk, "op": "eq", "value": "x"}
    with pytest.raises(ValidationError):
        exp.full_clean()
