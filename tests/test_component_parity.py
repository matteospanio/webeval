"""Built-in question types now live as QuestionComponent classes routed through
resolve_component(). These golden cases pin their validate_config / read_answer
/ default_config so the migration is (and stays) behaviour-preserving."""
from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError
from django.http import QueryDict

from experiments.question_types import (
    builtin_default_config,
    resolve_component,
)

BUILTINS = ["rating", "choice", "text", "likert", "numeric", "matrix", "ranking"]


class _Q:
    """Minimal stand-in for a Question (read_answer only touches pk/config/required)."""

    def __init__(self, pk=1, config=None, required=True):
        self.pk = pk
        self.config = config or {}
        self.required = required


def _post(data: dict) -> QueryDict:
    q = QueryDict(mutable=True)
    for k, v in data.items():
        if isinstance(v, list):
            for item in v:
                q.appendlist(k, item)
        else:
            q[k] = v
    return q


def test_every_builtin_resolves():
    for t in BUILTINS:
        assert resolve_component(t) is not None
    assert resolve_component("does_not_exist") is None


def test_default_configs_match_the_old_builder_defaults():
    assert builtin_default_config() == {
        "rating": {"min": 0, "max": 100, "step": 1},
        "choice": {"choices": ["Option 1", "Option 2"], "multi": False},
        "text": {"max_length": 500},
        "likert": {
            "steps": 5,
            "labels": ["Strongly disagree", "Disagree", "Neutral", "Agree", "Strongly agree"],
        },
        "numeric": {},
        "matrix": {"rows": ["Row 1"], "columns": ["Column 1", "Column 2"]},
        "ranking": {"items": ["Item 1", "Item 2"]},
    }


# --- validate_config golden cases -------------------------------------------


def test_rating_validation():
    c = resolve_component("rating")
    c.validate_config({"min": 0, "max": 10, "step": 1})
    with pytest.raises(ValidationError):
        c.validate_config({"min": 0, "max": 10})  # missing step
    with pytest.raises(ValidationError):
        c.validate_config({"min": 5, "max": 5, "step": 1})  # min !< max
    with pytest.raises(ValidationError):
        c.validate_config({"min": 0, "max": 10, "step": 0})  # step <= 0


def test_choice_validation():
    c = resolve_component("choice")
    c.validate_config({"choices": ["a", "b"], "multi": True})
    with pytest.raises(ValidationError):
        c.validate_config({"choices": []})
    with pytest.raises(ValidationError):
        c.validate_config({"choices": ["a", ""]})


def test_likert_validation():
    c = resolve_component("likert")
    c.validate_config({"steps": 3, "labels": ["a", "b", "c"]})
    with pytest.raises(ValidationError):
        c.validate_config({"steps": 3, "labels": ["a", "b"]})  # wrong count
    with pytest.raises(ValidationError):
        c.validate_config({"steps": 1, "labels": ["a"]})  # out of range


def test_numeric_matrix_ranking_validation():
    resolve_component("numeric").validate_config({"min": 0, "max": 10, "integer": True})
    with pytest.raises(ValidationError):
        resolve_component("numeric").validate_config({"min": 10, "max": 0})
    resolve_component("matrix").validate_config({"rows": ["r"], "columns": ["c1", "c2"]})
    with pytest.raises(ValidationError):
        resolve_component("matrix").validate_config({"rows": ["r", "r"], "columns": ["c"]})
    resolve_component("ranking").validate_config({"items": ["a", "b", "c"]})
    with pytest.raises(ValidationError):
        resolve_component("ranking").validate_config({"items": ["a"]})  # < 2


# --- read_answer golden cases ------------------------------------------------


def test_rating_read():
    c = resolve_component("rating")
    assert c.read_answer(_post({"q_1": "7"}), _Q()) == (True, 7, None)
    assert c.read_answer(_post({"q_1": ""}), _Q()) == (False, None, None)
    assert c.read_answer(_post({"q_1": "x"}), _Q())[:2] == (True, None)


def test_choice_read_single_and_multi():
    single = resolve_component("choice")
    assert single.read_answer(_post({"q_1": "a"}), _Q(config={"multi": False})) == (True, "a", None)
    multi_q = _Q(config={"multi": True})
    assert resolve_component("choice").read_answer(
        _post({"q_1": ["a", "b"]}), multi_q
    ) == (True, ["a", "b"], None)
    assert resolve_component("choice").read_answer(_post({}), multi_q) == (False, None, None)


def test_numeric_read_bounds_and_integer():
    c = resolve_component("numeric")
    q = _Q(config={"min": 0, "max": 10, "integer": True})
    assert c.read_answer(_post({"q_1": "5"}), q) == (True, 5, None)
    assert c.read_answer(_post({"q_1": "5.5"}), q)[:2] == (True, None)  # not whole
    assert c.read_answer(_post({"q_1": "-1"}), q)[:2] == (True, None)  # below min
    assert c.read_answer(_post({"q_1": "abc"}), q)[:2] == (True, None)


def test_matrix_read():
    c = resolve_component("matrix")
    q = _Q(config={"rows": ["r0", "r1"], "columns": ["c0", "c1"]})
    assert c.read_answer(_post({"q_1_r0": "c0", "q_1_r1": "c1"}), q) == (
        True, {"r0": "c0", "r1": "c1"}, None,
    )
    assert c.read_answer(_post({"q_1_r0": "c0"}), q)[:2] == (True, None)  # missing row
    assert c.read_answer(_post({"q_1_r0": "bad"}), q)[:2] == (True, None)  # invalid col


def test_ranking_read():
    c = resolve_component("ranking")
    q = _Q(config={"items": ["a", "b", "c"]})
    assert c.read_answer(_post({"q_1_i0": "2", "q_1_i1": "1", "q_1_i2": "3"}), q) == (
        True, ["b", "a", "c"], None,
    )
    assert c.read_answer(_post({"q_1_i0": "1", "q_1_i1": "1", "q_1_i2": "3"}), q)[:2] == (
        True, None,  # duplicate rank
    )
