"""Activation-readiness checks (Epic 5)."""
from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError

from experiments.models import Experiment
from experiments.readiness import is_walkable, readiness_problems
from experiments.tests.factories import (
    ConditionFactory,
    ExperimentFactory,
    RatingQuestionFactory,
    TextStimulusFactory,
)

pytestmark = pytest.mark.django_db


def _complete_draft(**kw):
    exp = ExperimentFactory(**kw)
    cond = ConditionFactory(experiment=exp)
    TextStimulusFactory(condition=cond)
    RatingQuestionFactory(experiment=exp)
    return exp


def test_empty_draft_has_problems():
    exp = ExperimentFactory()
    problems = readiness_problems(exp)
    assert any("condition" in p for p in problems)
    assert any("stimulus" in p for p in problems)
    assert any("question" in p for p in problems)
    assert not is_walkable(exp)


def test_complete_draft_is_ready():
    exp = _complete_draft()
    assert readiness_problems(exp) == []
    assert is_walkable(exp)


def test_consent_text_required():
    exp = _complete_draft(consent_text="")
    assert any("consent" in p.lower() for p in readiness_problems(exp))


def test_stimuli_per_participant_cannot_exceed_available():
    exp = _complete_draft(stimuli_per_participant=5)  # only one active stimulus
    assert any("exceeds" in p for p in readiness_problems(exp))


def test_clean_blocks_activation_when_incomplete():
    exp = ExperimentFactory()
    exp.state = Experiment.State.ACTIVE
    with pytest.raises(ValidationError):
        exp.full_clean()


def test_clean_allows_activation_when_complete():
    exp = _complete_draft()
    exp.state = Experiment.State.ACTIVE
    exp.full_clean()  # must not raise


def test_clean_blocks_preview_when_not_walkable():
    exp = ExperimentFactory()
    exp.state = Experiment.State.TEST
    with pytest.raises(ValidationError):
        exp.full_clean()
