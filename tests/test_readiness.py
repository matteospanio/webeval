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


def test_unregistered_strategy_blocks_activation():
    exp = _complete_draft(assignment_strategy="does_not_exist")
    assert any("not a registered standard strategy" in p for p in readiness_problems(exp))


def test_pairwise_strategy_on_standard_study_blocks_activation():
    # The admin dropdown offers both registries' names for the one field; a
    # cross-mode pick must not silently fall back to balanced_random.
    exp = _complete_draft(assignment_strategy="pairwise_balanced")
    assert any("not a registered standard strategy" in p for p in readiness_problems(exp))


def test_registered_strategy_is_fine():
    exp = _complete_draft(assignment_strategy="counterbalanced")
    assert readiness_problems(exp) == []


def test_unrenderable_question_type_blocks_activation():
    from experiments.models import Question

    exp = _complete_draft()
    Question.objects.create(
        experiment=exp,
        section=Question.Section.STIMULUS,
        type="gone_plugin",
        prompt="Orphaned plugin question",
        config={},
    )
    assert any("not installed on this server" in p for p in readiness_problems(exp))


def test_pairwise_study_on_model_default_strategy_is_fine():
    # The model default is the standard-mode name "balanced_random"; on a
    # pairwise study the runtime maps it to the pairwise default, so leaving
    # it untouched must not block activation.
    exp = _complete_draft(mode=Experiment.Mode.PAIRWISE)
    TextStimulusFactory(condition=exp.conditions.first())  # pairwise needs >= 2
    assert exp.assignment_strategy == "balanced_random"
    assert not any("strategy" in p.lower() for p in readiness_problems(exp))
