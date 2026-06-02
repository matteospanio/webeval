"""Pluggable question-type components — registry + model validation (plugin system)."""
from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError

from experiments.components import (
    BUILTIN_TYPES,
    QuestionComponent,
    available_question_components,
    get_question_component,
    is_question_component,
    register_question_component,
)
from experiments.models import Question
from experiments.tests.factories import ExperimentFactory

pytestmark = pytest.mark.django_db


def test_shipped_constant_sum_is_registered():
    assert is_question_component("constant_sum")
    comp = get_question_component("constant_sum")
    assert comp.label
    assert comp in available_question_components()


def test_register_rejects_bad_keys():
    class _Dummy(QuestionComponent):
        type = ""

    with pytest.raises(ValueError):
        register_question_component(_Dummy())

    class _Shadow(QuestionComponent):
        type = next(iter(BUILTIN_TYPES))  # collides with a built-in

    with pytest.raises(ValueError):
        register_question_component(_Shadow())

    class _TooLong(QuestionComponent):
        type = "x" * 20

    with pytest.raises(ValueError):
        register_question_component(_TooLong())


def test_constant_sum_config_validation():
    comp = get_question_component("constant_sum")
    comp.validate_config({"items": ["A", "B"], "total": 100})  # ok
    with pytest.raises(ValidationError):
        comp.validate_config({"items": ["only-one"]})
    with pytest.raises(ValidationError):
        comp.validate_config({"items": ["A", "B"], "total": 0})
    with pytest.raises(ValidationError):
        comp.validate_config({"items": ["A", "A"], "total": 100})  # not distinct


def _plugin_question(exp, config):
    return Question(
        experiment=exp,
        section=Question.Section.STIMULUS,
        type="constant_sum",
        prompt="Split your budget",
        config=config,
    )


def test_question_with_plugin_type_passes_full_clean():
    exp = ExperimentFactory()
    q = _plugin_question(exp, {"items": ["Rent", "Food"], "total": 100})
    q.full_clean()  # clean_fields accepts the type; component validates config
    q.save()
    assert exp.questions.get().type == "constant_sum"


def test_question_with_bad_plugin_config_rejected():
    exp = ExperimentFactory()
    q = _plugin_question(exp, {"items": ["only-one"]})
    with pytest.raises(ValidationError):
        q.full_clean()


def test_unknown_question_type_still_rejected():
    exp = ExperimentFactory()
    q = Question(
        experiment=exp,
        section=Question.Section.STIMULUS,
        type="totally_bogus",
        prompt="?",
        config={},
    )
    with pytest.raises(ValidationError):
        q.full_clean()
