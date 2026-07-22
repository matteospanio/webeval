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


# --- loose comparison parity with survey/js/branching.js --------------------
# The JS mirror reads DOM strings, so both engines compare numbers numerically
# whenever both sides parse and fall back to string equality. These tests pin
# the Python half of that contract.


def test_eq_is_type_tolerant():
    # A rating stored as int 3 matches a rule authored with the string "3".
    assert evaluate_condition({"question": 1, "op": "eq", "value": "3"}, {1: 3})
    assert evaluate_condition({"question": 1, "op": "eq", "value": 3}, {1: "3"})
    assert evaluate_condition({"question": 1, "op": "eq", "value": 3.0}, {1: 3})
    assert not evaluate_condition({"question": 1, "op": "eq", "value": "4"}, {1: 3})


def test_ne_is_type_tolerant():
    # Regression: with typed equality, {op:"ne", value:"3"} against int 3 was
    # True server-side but False in JS — the dependent could deadlock (JS
    # hides+disables it, server keeps requiring it).
    assert not evaluate_condition({"question": 1, "op": "ne", "value": "3"}, {1: 3})
    assert evaluate_condition({"question": 1, "op": "ne", "value": "3"}, {1: 4})


def test_in_nin_loose_membership():
    assert evaluate_condition({"question": 1, "op": "in", "value": ["3", "5"]}, {1: 3})
    assert not evaluate_condition({"question": 1, "op": "nin", "value": [3]}, {1: "3"})


def test_in_with_string_target_is_substring():
    assert evaluate_condition({"question": 1, "op": "in", "value": "abc"}, {1: "b"})
    assert not evaluate_condition({"question": 1, "op": "in", "value": "abc"}, {1: "z"})


def test_nin_with_null_target_is_true():
    assert evaluate_condition({"question": 1, "op": "nin", "value": None}, {1: "x"})
    assert not evaluate_condition({"question": 1, "op": "in", "value": None}, {1: "x"})


def test_contains_is_loose_for_list_answers():
    # Multi-choice answers are lists of strings; a numeric rule value matches.
    assert evaluate_condition({"question": 1, "op": "contains", "value": 3}, {1: ["3", "5"]})
    assert evaluate_condition({"question": 1, "op": "contains", "value": "b"}, {1: "abc"})
    assert not evaluate_condition({"question": 1, "op": "contains", "value": "z"}, {1: ["a"]})


def test_strings_that_do_not_parse_compare_as_strings():
    assert evaluate_condition({"question": 1, "op": "eq", "value": "Yes"}, {1: "Yes"})
    assert not evaluate_condition({"question": 1, "op": "eq", "value": "Yes"}, {1: "No"})


def test_contains_coerces_scalar_answers():
    # A rating stored as int 35 "contains" the digit "5" — the JS mirror can
    # only see the DOM string, so the server coerces scalars the same way.
    assert evaluate_condition({"question": 1, "op": "contains", "value": "5"}, {1: 35})
    assert evaluate_condition({"question": 1, "op": "contains", "value": 3}, {1: 3.5})
    assert not evaluate_condition({"question": 1, "op": "contains", "value": "9"}, {1: 35})


def test_list_answers_compare_elementwise_loose():
    # Checkbox answers are strings in the DOM but a rule may be authored with
    # numbers — element-wise loose equality keeps the engines in lockstep.
    assert evaluate_condition({"question": 1, "op": "eq", "value": [3]}, {1: ["3"]})
    assert not evaluate_condition({"question": 1, "op": "ne", "value": [3]}, {1: ["3"]})
    assert not evaluate_condition({"question": 1, "op": "eq", "value": [3, 4]}, {1: ["3"]})


def test_array_answer_never_in_string_target():
    # JS String([]) === "" would vacuously match any string target; both
    # engines therefore refuse membership for non-scalar answers.
    assert not evaluate_condition({"question": 1, "op": "in", "value": "abc"}, {1: []})
    assert evaluate_condition({"question": 1, "op": "nin", "value": "abc"}, {1: ["a"]})


def test_shared_numeric_grammar_excludes_python_only_literals():
    # float() accepts "1_0"/"inf"/"nan" but JS Number() does not — the shared
    # grammar treats them all as plain strings on both sides.
    assert not evaluate_condition({"question": 1, "op": "eq", "value": 10}, {1: "1_0"})
    assert not evaluate_condition({"question": 1, "op": "gt", "value": 3}, {1: "inf"})
    assert evaluate_condition({"question": 1, "op": "eq", "value": "nan"}, {1: "nan"})
    assert evaluate_condition({"question": 1, "op": "eq", "value": "3e1"}, {1: 30})


def test_boolean_values_stringify_like_js():
    assert evaluate_condition({"question": 1, "op": "eq", "value": True}, {1: "true"})
    assert not evaluate_condition({"question": 1, "op": "ne", "value": True}, {1: "true"})
