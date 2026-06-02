"""Between-subject assignment through the participant flow."""
from __future__ import annotations

import pytest
from django.test import Client
from django.urls import reverse

from experiments.models import Experiment
from experiments.tests.factories import (
    ConditionFactory,
    ExperimentFactory,
    RatingQuestionFactory,
    TextStimulusFactory,
)
from survey.models import ParticipantSession

pytestmark = pytest.mark.django_db


def test_between_subject_flow_assigns_a_single_condition():
    exp = ExperimentFactory(
        slug="bs",
        require_audio_check=False,
        assignment_strategy="between_subject",
    )
    for name in ("A", "B"):
        cond = ConditionFactory(experiment=exp, name=name)
        for j in range(2):
            TextStimulusFactory(condition=cond, title=f"{name}{j}", sort_order=j)
    RatingQuestionFactory(experiment=exp, sort_order=0)
    exp.state = Experiment.State.ACTIVE
    exp.save(update_fields=["state"])

    client = Client()
    client.post(reverse("survey:consent", kwargs={"slug": exp.slug}), {"agree": "on"})
    client.post(reverse("survey:instructions", kwargs={"slug": exp.slug}))

    session = ParticipantSession.objects.get()
    assert session.assigned_condition_id is not None
    assigned = {a.stimulus.condition_id for a in session.assignments.all()}
    assert len(assigned) == 1
    assert next(iter(assigned)) == session.assigned_condition_id
