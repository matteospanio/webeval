"""Unit tests for the per-question answer readers (numeric / matrix / ranking)."""
from __future__ import annotations

import pytest
from django.test import RequestFactory

from experiments.tests.factories import (
    MatrixQuestionFactory,
    NumericQuestionFactory,
    RankingQuestionFactory,
)
from survey.views import _read_one

pytestmark = pytest.mark.django_db

rf = RequestFactory()


def _read(question, data):
    return _read_one(rf.post("/x/", data), question)


# --- numeric ----------------------------------------------------------------


def test_numeric_happy_path_coerces_int():
    q = NumericQuestionFactory()  # min 0, max 120, integer, unit
    answered, value, err = _read(q, {f"q_{q.pk}": "30"})
    assert answered and err is None and value == 30 and isinstance(value, int)


def test_numeric_out_of_range():
    q = NumericQuestionFactory()
    _, value, err = _read(q, {f"q_{q.pk}": "200"})
    assert value is None and err is not None


def test_numeric_non_integer_rejected_when_integer_required():
    q = NumericQuestionFactory()
    _, value, err = _read(q, {f"q_{q.pk}": "30.5"})
    assert value is None and err is not None


def test_numeric_optional_blank_is_skipped():
    q = NumericQuestionFactory(required=False)
    answered, value, err = _read(q, {})
    assert not answered and err is None


def test_numeric_float_allowed_when_not_integer():
    q = NumericQuestionFactory(config={"min": 0, "max": 10})
    answered, value, err = _read(q, {f"q_{q.pk}": "3.5"})
    assert answered and err is None and value == 3.5


# --- matrix -----------------------------------------------------------------


def test_matrix_happy_path_returns_row_dict():
    q = MatrixQuestionFactory()  # rows Clarity, Musicality; cols Low/Medium/High
    answered, value, err = _read(
        q, {f"q_{q.pk}_r0": "High", f"q_{q.pk}_r1": "Low"}
    )
    assert err is None
    assert value == {"Clarity": "High", "Musicality": "Low"}


def test_matrix_incomplete_when_required():
    q = MatrixQuestionFactory()
    _, value, err = _read(q, {f"q_{q.pk}_r0": "High"})
    assert value is None and err is not None


def test_matrix_invalid_column_rejected():
    q = MatrixQuestionFactory()
    _, value, err = _read(q, {f"q_{q.pk}_r0": "Nope", f"q_{q.pk}_r1": "Low"})
    assert value is None and err is not None


# --- ranking ----------------------------------------------------------------


def test_ranking_happy_path_returns_ordered_items():
    q = RankingQuestionFactory()  # items A, B, C
    answered, value, err = _read(
        q, {f"q_{q.pk}_i0": "2", f"q_{q.pk}_i1": "1", f"q_{q.pk}_i2": "3"}
    )
    assert err is None
    assert value == ["B", "A", "C"]  # rank 1 = B, rank 2 = A, rank 3 = C


def test_ranking_incomplete_rejected():
    q = RankingQuestionFactory()
    _, value, err = _read(q, {f"q_{q.pk}_i0": "1"})
    assert value is None and err is not None


def test_ranking_duplicate_ranks_rejected():
    q = RankingQuestionFactory()
    _, value, err = _read(
        q, {f"q_{q.pk}_i0": "1", f"q_{q.pk}_i1": "1", f"q_{q.pk}_i2": "2"}
    )
    assert value is None and err is not None
