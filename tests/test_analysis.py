"""Per-question analytics + hand-rolled statistical tests (Epic 6)."""
from __future__ import annotations

import json

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from accounts.tests.factories import UserFactory
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


def _session(exp, preview=False, device="desktop", country="US"):
    return ParticipantSession.objects.create(
        experiment=exp,
        last_step=ParticipantSession.Step.DONE,
        consented_at=timezone.now(),
        submitted_at=timezone.now(),
        is_preview=preview,
        device_type=device,
        country_code=country,
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


def test_segmented_analysis_groups_by_device():
    from experiments.analysis import segmented_question_analysis

    exp = ExperimentFactory()
    q = ChoiceQuestionFactory(
        experiment=exp, config={"choices": ["yes", "no"], "multi": False}
    )
    for _ in range(2):
        _resp(_session(exp, device="desktop"), q, "yes")
    for _ in range(3):
        _resp(_session(exp, device="mobile"), q, "no")

    result = segmented_question_analysis(exp, "device")
    segments = {s["label"]: s["result"] for s in result[0]["segments"]}
    assert set(segments) == {"desktop", "mobile"}
    assert segments["desktop"].n == 2 and segments["mobile"].n == 3


def test_segment_by_condition():
    from experiments.analysis import segmented_question_analysis

    exp = ExperimentFactory()
    _two_condition_rating(exp, [80, 82], [40, 42])  # responses tied to A / B stimuli
    result = segmented_question_analysis(exp, "condition")
    labels = {s["label"] for s in result[0]["segments"]}
    assert {"A", "B"} <= labels


def test_segment_by_cohort_buckets_by_week():
    from datetime import timedelta

    from experiments.analysis import segmented_question_analysis

    exp = ExperimentFactory()
    q = RatingQuestionFactory(experiment=exp)
    s1 = _session(exp)
    _resp(s1, q, 50)
    s2 = _session(exp)
    s2.submitted_at = timezone.now() - timedelta(days=14)
    s2.save(update_fields=["submitted_at"])
    _resp(s2, q, 60)

    result = segmented_question_analysis(exp, "cohort")
    weeks = {s["label"] for s in result[0]["segments"]}
    assert len(weeks) == 2  # two distinct ISO-week cohorts


def test_studio_overview_segment_view_renders():
    owner = UserFactory()
    exp = ExperimentFactory(owner=owner)
    q = ChoiceQuestionFactory(
        experiment=exp, config={"choices": ["yes", "no"], "multi": False}
    )
    _resp(_session(exp, device="desktop"), q, "yes")
    _resp(_session(exp, device="mobile"), q, "no")
    client = Client()
    client.force_login(owner)
    url = reverse("studio:study_overview", kwargs={"slug": exp.slug})
    body = client.get(url + "?segment=device").content.decode()
    assert "desktop" in body and "mobile" in body


def test_studio_overview_renders_per_question_results():
    owner = UserFactory()
    exp = ExperimentFactory(owner=owner)
    q = _two_condition_rating(exp, [90, 91, 89], [10, 11, 9])
    q.prompt = "Overall quality"
    q.save(update_fields=["prompt"])
    client = Client()
    client.force_login(owner)
    body = client.get(
        reverse("studio:study_overview", kwargs={"slug": exp.slug})
    ).content.decode()
    assert "Per-question results" in body
    assert "Overall quality" in body
    assert "One-way ANOVA" in body
