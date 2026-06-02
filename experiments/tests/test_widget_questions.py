"""Validation tests for the numeric / matrix / ranking question types:
model-level config validation and the QuestionAdminForm round-trip.
"""
from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError

from experiments.forms import QuestionAdminForm
from experiments.models import Question, _validate_question_config
from experiments.tests.factories import ExperimentFactory

pytestmark = pytest.mark.django_db


# --- model-level config validation -----------------------------------------


def test_numeric_config_valid():
    _validate_question_config(Question.Type.NUMERIC, {})  # everything optional
    _validate_question_config(
        Question.Type.NUMERIC, {"min": 0, "max": 10, "integer": True, "unit": "yr"}
    )


@pytest.mark.parametrize(
    "cfg",
    [
        {"min": "x"},
        {"min": True},
        {"min": 5, "max": 5},
        {"min": 10, "max": 1},
        {"integer": "yes"},
        {"unit": 3},
    ],
)
def test_numeric_config_invalid(cfg):
    with pytest.raises(ValidationError):
        _validate_question_config(Question.Type.NUMERIC, cfg)


def test_matrix_config_valid():
    _validate_question_config(
        Question.Type.MATRIX, {"rows": ["a", "b"], "columns": ["1", "2"]}
    )


@pytest.mark.parametrize(
    "cfg",
    [
        {"rows": [], "columns": ["1"]},
        {"rows": ["a", "a"], "columns": ["1"]},
        {"rows": ["a"]},  # missing columns
        {"rows": ["a"], "columns": []},
        {"columns": ["1"]},  # missing rows
    ],
)
def test_matrix_config_invalid(cfg):
    with pytest.raises(ValidationError):
        _validate_question_config(Question.Type.MATRIX, cfg)


def test_ranking_config_valid():
    _validate_question_config(Question.Type.RANKING, {"items": ["a", "b", "c"]})


@pytest.mark.parametrize(
    "cfg",
    [
        {"items": ["only-one"]},
        {"items": ["a", "a"]},
        {"items": "abc"},
        {},
    ],
)
def test_ranking_config_invalid(cfg):
    with pytest.raises(ValidationError):
        _validate_question_config(Question.Type.RANKING, cfg)


# --- QuestionAdminForm round-trip -------------------------------------------


def _form_data(exp, qtype, **extra):
    data = {
        "experiment": exp.pk,
        "section": Question.Section.STIMULUS,
        "type": qtype,
        "prompt": "Q?",
        "required": "on",
        "sort_order": 0,
    }
    data.update(extra)
    return data


def test_admin_form_builds_numeric_config():
    exp = ExperimentFactory()
    form = QuestionAdminForm(
        data=_form_data(
            exp,
            Question.Type.NUMERIC,
            numeric_min=0,
            numeric_max=120,
            numeric_integer="on",
            numeric_unit="years",
        )
    )
    assert form.is_valid(), form.errors
    assert form.instance.config == {
        "min": 0.0,
        "max": 120.0,
        "integer": True,
        "unit": "years",
    }


def test_admin_form_builds_matrix_config():
    exp = ExperimentFactory()
    form = QuestionAdminForm(
        data=_form_data(
            exp,
            Question.Type.MATRIX,
            matrix_rows="Clarity\nTone",
            matrix_columns="Low\nHigh",
        )
    )
    assert form.is_valid(), form.errors
    assert form.instance.config == {
        "rows": ["Clarity", "Tone"],
        "columns": ["Low", "High"],
    }


def test_admin_form_rejects_duplicate_ranking_items():
    exp = ExperimentFactory()
    form = QuestionAdminForm(
        data=_form_data(exp, Question.Type.RANKING, ranking_items="A\nA\nB")
    )
    assert not form.is_valid()
    assert "ranking_items" in form.errors
