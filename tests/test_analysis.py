"""Per-question analytics + hand-rolled statistical tests (Epic 6)."""
from __future__ import annotations

import json

import pytest
from django.utils import timezone

from experiments import stats_tests as st
from experiments.analysis import analyse_question, experiment_question_analysis
from experiments.models import Question
from experiments.stats_tests import compare_conditions
from experiments.tests.factories import (
    ChoiceQuestionFactory,
    ConditionFactory,
    ExperimentFactory,
    RatingQuestionFactory,
    StimulusFactory,
)
from survey.models import ParticipantSession, Response

pytestmark = pytest.mark.django_db


def _session(exp, preview=False):
    return ParticipantSession.objects.create(
        experiment=exp,
        last_step=ParticipantSession.Step.DONE,
        consented_at=timezone.now(),
        submitted_at=timezone.now(),
        is_preview=preview,
        device_type="desktop",
        country_code="US",
    )


def _resp(session, question, value, stimulus=None):
    Response.objects.create(
        session=session, question=question, stimulus=stimulus,
        answer_value=json.dumps(value),
    )


# --- descriptive analytics --------------------------------------------------


def test_choice_distribution():
    exp = ExperimentFactory()
    q = ChoiceQuestionFactory(
        experiment=exp, config={"choices": ["female", "male", "other"], "multi": False}
    )
    for val in ["male", "male", "female"]:
        _resp(_session(exp), q, val)
    res = analyse_question(exp, q)
    assert res.kind == "distribution" and res.n == 3
    counts = {r["label"]: r["count"] for r in res.rows}
    assert counts["male"] == 2 and counts["female"] == 1 and counts["other"] == 0


def test_rating_numeric_summary_excludes_preview():
    exp = ExperimentFactory()
    q = RatingQuestionFactory(experiment=exp)
    for v in (10, 20, 30):
        _resp(_session(exp), q, v)
    _resp(_session(exp, preview=True), q, 1000)  # preview ignored
    res = analyse_question(exp, q)
    assert res.kind == "numeric"
    assert res.stats["n"] == 3 and res.stats["mean"] == 20.0
    assert res.stats["min"] == 10.0 and res.stats["max"] == 30.0


def test_experiment_analysis_covers_every_question():
    exp = ExperimentFactory()
    RatingQuestionFactory(experiment=exp, sort_order=0)
    ChoiceQuestionFactory(experiment=exp, sort_order=1)
    results = experiment_question_analysis(exp)
    assert {r.type for r in results} == {"rating", "choice"}


# --- special-function correctness (anchors from standard tables) ------------


def test_special_functions_match_known_values():
    # I_0.5(0.5, 0.5) = 0.5 exactly.
    assert st.betainc(0.5, 0.5, 0.5) == pytest.approx(0.5, abs=1e-6)
    # chi-square 3.841 @ df=1 -> p = 0.05; zero statistic -> p = 1.
    assert st.chi2_sf(3.8414588, 1) == pytest.approx(0.05, abs=2e-3)
    assert st.chi2_sf(0.0, 4) == pytest.approx(1.0, abs=1e-9)
    # Student t: 2.2281 @ df=10 -> two-sided p = 0.05; t=0 -> p = 1.
    assert st.t_sf_two_sided(2.2281, 10) == pytest.approx(0.05, abs=2e-3)
    assert st.t_sf_two_sided(0.0, 5) == pytest.approx(1.0, abs=1e-9)


def test_f_equals_t_squared_for_two_groups():
    # For 1 numerator d.o.f., F = t^2 and the tail probabilities coincide.
    t, df2 = 1.7, 8
    assert st.f_sf(t * t, 1, df2) == pytest.approx(st.t_sf_two_sided(t, df2), abs=1e-6)


def test_anova_and_chi_square_statistics():
    assert st.one_way_anova([[1, 1, 1], [9, 9, 9]])["p_value"] < 0.01
    big = st.one_way_anova([[1, 2, 3], [1, 2, 3]])
    assert big["p_value"] > 0.5  # identical groups -> no effect
    assert st.chi_square_test([[10, 0], [0, 10]])["p_value"] < 0.01


# --- compare across conditions ----------------------------------------------


def _two_condition_rating(exp, a_values, b_values):
    cond_a = ConditionFactory(experiment=exp, name="A")
    cond_b = ConditionFactory(experiment=exp, name="B")
    sa = StimulusFactory(condition=cond_a, title="a")
    sb = StimulusFactory(condition=cond_b, title="b")
    q = RatingQuestionFactory(experiment=exp)
    for v in a_values:
        _resp(_session(exp), q, v, stimulus=sa)
    for v in b_values:
        _resp(_session(exp), q, v, stimulus=sb)
    return q


def test_compare_conditions_detects_difference():
    exp = ExperimentFactory()
    q = _two_condition_rating(exp, [90, 91, 89, 92], [10, 11, 9, 12])
    res = compare_conditions(exp, q)
    assert res["applicable"] and res["test"] == "One-way ANOVA"
    assert res["p_value"] < 0.01
    assert {g["condition"] for g in res["groups"]} == {"A", "B"}


def test_compare_conditions_no_difference():
    exp = ExperimentFactory()
    q = _two_condition_rating(exp, [50, 51, 49], [50, 49, 51])
    assert compare_conditions(exp, q)["p_value"] > 0.05


def test_compare_conditions_choice_uses_chi_square():
    exp = ExperimentFactory()
    cond_a = ConditionFactory(experiment=exp, name="A")
    cond_b = ConditionFactory(experiment=exp, name="B")
    sa = StimulusFactory(condition=cond_a, title="a")
    sb = StimulusFactory(condition=cond_b, title="b")
    q = ChoiceQuestionFactory(
        experiment=exp, section=Question.Section.STIMULUS,
        config={"choices": ["yes", "no"], "multi": False},
    )
    for v in ["yes"] * 8 + ["no"]:
        _resp(_session(exp), q, v, stimulus=sa)
    for v in ["no"] * 8 + ["yes"]:
        _resp(_session(exp), q, v, stimulus=sb)
    res = compare_conditions(exp, q)
    assert res["applicable"] and res["test"] == "Chi-square"
    assert res["p_value"] < 0.01


def test_compare_conditions_not_applicable_for_demographic():
    exp = ExperimentFactory()
    q = ChoiceQuestionFactory(experiment=exp)  # default section = demographic
    assert compare_conditions(exp, q)["applicable"] is False
