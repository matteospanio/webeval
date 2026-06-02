"""Conditional-display engine + visible_if validation."""
from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError

from experiments.branching import evaluate_condition, is_visible
from experiments.models import Question
from experiments.tests.factories import (
    ChoiceQuestionFactory,
    ExperimentFactory,
    RatingQuestionFactory,
)

# --- engine (pure) ----------------------------------------------------------


def test_eq_and_missing_controller():
    rule = {"question": 1, "op": "eq", "value": "Yes"}
    assert evaluate_condition(rule, {1: "Yes"})
    assert not evaluate_condition(rule, {1: "No"})
    assert not evaluate_condition(rule, {})  # controller unanswered → hidden


def test_in_nin():
    assert evaluate_condition({"question": 1, "op": "in", "value": ["a", "b"]}, {1: "a"})
    assert not evaluate_condition({"question": 1, "op": "in", "value": ["a", "b"]}, {1: "c"})
    assert evaluate_condition({"question": 1, "op": "nin", "value": ["a"]}, {1: "z"})


def test_numeric_ops_and_bad_types():
    assert evaluate_condition({"question": 1, "op": "gte", "value": 18}, {1: 21})
    assert not evaluate_condition({"question": 1, "op": "gte", "value": 18}, {1: 16})
    # non-numeric answer with a numeric op degrades to False, not an error
    assert not evaluate_condition({"question": 1, "op": "gt", "value": 18}, {1: "abc"})


def test_contains_multichoice():
    assert evaluate_condition({"question": 1, "op": "contains", "value": "x"}, {1: ["x", "y"]})
    assert not evaluate_condition({"question": 1, "op": "contains", "value": "z"}, {1: ["x", "y"]})


def test_answered_not_answered():
    assert evaluate_condition({"question": 1, "op": "answered"}, {1: "Yes"})
    assert not evaluate_condition({"question": 1, "op": "answered"}, {1: ""})
    assert evaluate_condition({"question": 1, "op": "not_answered"}, {})


def test_all_any():
    rule_all = {"all": [
        {"question": 1, "op": "eq", "value": "Yes"},
        {"question": 2, "op": "gte", "value": 18},
    ]}
    assert evaluate_condition(rule_all, {1: "Yes", 2: 20})
    assert not evaluate_condition(rule_all, {1: "Yes", 2: 10})
    rule_any = {"any": [
        {"question": 1, "op": "eq", "value": "Yes"},
        {"question": 2, "op": "gte", "value": 18},
    ]}
    assert evaluate_condition(rule_any, {1: "No", 2: 20})
    assert not evaluate_condition(rule_any, {1: "No", 2: 10})


def test_empty_rule_is_always_visible():
    assert evaluate_condition({}, {}) is True
    assert is_visible(type("Q", (), {"visible_if": {}})(), {}) is True


# --- model validation -------------------------------------------------------

pytestmark = pytest.mark.django_db


def _choice(exp, **kw):
    kw.setdefault("section", Question.Section.DEMOGRAPHIC)
    kw.setdefault("config", {"choices": ["Yes", "No"], "multi": False})
    return ChoiceQuestionFactory(experiment=exp, **kw)


def test_valid_visible_if_passes():
    exp = ExperimentFactory()
    ctrl = _choice(exp, sort_order=0)
    dep = _choice(
        exp,
        sort_order=1,
        config={"choices": ["a", "b"], "multi": False},
        visible_if={"question": ctrl.pk, "op": "eq", "value": "Yes"},
    )
    dep.full_clean()  # must not raise


def test_reject_forward_reference():
    exp = ExperimentFactory()
    ctrl = _choice(exp, sort_order=5)
    dep = ChoiceQuestionFactory.build(
        experiment=exp,
        section=Question.Section.DEMOGRAPHIC,
        sort_order=1,
        config={"choices": ["a"], "multi": False},
        visible_if={"question": ctrl.pk, "op": "eq", "value": "Yes"},
    )
    with pytest.raises(ValidationError):
        dep.full_clean()


def test_reject_cross_section_reference():
    exp = ExperimentFactory()
    ctrl = RatingQuestionFactory(experiment=exp, section=Question.Section.STIMULUS, sort_order=0)
    dep = ChoiceQuestionFactory.build(
        experiment=exp,
        section=Question.Section.DEMOGRAPHIC,
        sort_order=1,
        config={"choices": ["a"], "multi": False},
        visible_if={"question": ctrl.pk, "op": "eq", "value": "Yes"},
    )
    with pytest.raises(ValidationError):
        dep.full_clean()


def test_reject_unknown_operator():
    exp = ExperimentFactory()
    ctrl = _choice(exp, sort_order=0)
    dep = ChoiceQuestionFactory.build(
        experiment=exp,
        section=Question.Section.DEMOGRAPHIC,
        sort_order=1,
        config={"choices": ["a"], "multi": False},
        visible_if={"question": ctrl.pk, "op": "frobnicate", "value": "x"},
    )
    with pytest.raises(ValidationError):
        dep.full_clean()
