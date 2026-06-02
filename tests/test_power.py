"""Power / sample-size analysis (Epic 6)."""
from __future__ import annotations

import json

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from accounts.tests.factories import UserFactory
from experiments.power import (
    achieved_power,
    cohens_d,
    norm_cdf,
    norm_ppf,
    required_n_per_group,
)
from experiments.tests.factories import (
    ConditionFactory,
    ExperimentFactory,
    RatingQuestionFactory,
    StimulusFactory,
)
from survey.models import ParticipantSession, Response

pytestmark = pytest.mark.django_db


def test_normal_helpers_match_known_values():
    assert norm_ppf(0.975) == pytest.approx(1.95996, abs=1e-3)
    assert norm_ppf(0.8) == pytest.approx(0.84162, abs=1e-3)
    assert norm_cdf(0.0) == pytest.approx(0.5, abs=1e-9)
    assert norm_cdf(1.95996) == pytest.approx(0.975, abs=1e-4)


def test_required_n_matches_textbook():
    # d=0.5, alpha=.05 two-sided, power=.8 -> ~63-64 per group.
    n = required_n_per_group(0.5, 0.05, 0.8)
    assert 60 <= n <= 66
    # Bigger effects need fewer subjects.
    assert required_n_per_group(0.8) < n < required_n_per_group(0.2)
    assert required_n_per_group(0) is None


def test_achieved_power_round_trips():
    assert achieved_power(0.5, 64) == pytest.approx(0.80, abs=0.03)
    assert achieved_power(0.0, 100) == 0.0


def test_cohens_d():
    assert cohens_d([1, 2, 3], [4, 5, 6]) == pytest.approx(-3.0, abs=1e-6)
    assert cohens_d([5, 5, 5], [5, 5, 5]) is None  # zero variance


def test_power_page_renders_calculator_and_pilot():
    owner = UserFactory()
    exp = ExperimentFactory(owner=owner)
    cond_a = ConditionFactory(experiment=exp, name="A")
    cond_b = ConditionFactory(experiment=exp, name="B")
    sa = StimulusFactory(condition=cond_a, title="a")
    sb = StimulusFactory(condition=cond_b, title="b")
    q = RatingQuestionFactory(experiment=exp, prompt="Quality")

    def _resp(stim, value):
        s = ParticipantSession.objects.create(
            experiment=exp, last_step=ParticipantSession.Step.DONE,
            consented_at=timezone.now(), submitted_at=timezone.now(),
        )
        Response.objects.create(
            session=s, stimulus=stim, question=q, answer_value=json.dumps(value)
        )

    for v in (70, 72, 68, 71):
        _resp(sa, v)
    for v in (40, 42, 38, 41):
        _resp(sb, v)

    client = Client()
    client.force_login(owner)
    body = client.get(
        reverse("studio:power_analysis", kwargs={"slug": exp.slug}) + "?d=0.5"
    ).content.decode()
    assert "Required sample size" in body
    assert "Quality" in body  # pilot row for the rating question
