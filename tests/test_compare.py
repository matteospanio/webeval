"""Cross-experiment comparison page (Epic 6)."""
from __future__ import annotations

import json

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from accounts.tests.factories import UserFactory
from experiments.tests.factories import (
    ConditionFactory,
    ExperimentFactory,
    RatingQuestionFactory,
    StimulusFactory,
)
from survey.models import ParticipantSession, Response

pytestmark = pytest.mark.django_db


def test_compare_lists_only_my_studies():
    owner = UserFactory()
    ExperimentFactory(owner=owner, name="My Study")
    ExperimentFactory(name="Someone Elses")  # owner is None → not visible
    client = Client()
    client.force_login(owner)
    body = client.get(reverse("studio:compare")).content.decode()
    assert "My Study" in body
    assert "Someone Elses" not in body


def test_compare_shows_metrics():
    owner = UserFactory()
    exp = ExperimentFactory(owner=owner, name="Rated Study")
    cond = ConditionFactory(experiment=exp, name="A")
    stim = StimulusFactory(condition=cond, title="s")
    q = RatingQuestionFactory(experiment=exp)
    session = ParticipantSession.objects.create(
        experiment=exp, last_step=ParticipantSession.Step.DONE,
        consented_at=timezone.now(), submitted_at=timezone.now(),
    )
    Response.objects.create(
        session=session, stimulus=stim, question=q, answer_value=json.dumps(80)
    )
    client = Client()
    client.force_login(owner)
    body = client.get(reverse("studio:compare")).content.decode()
    assert "Rated Study" in body
    assert "mean 80" in body  # headline metric
