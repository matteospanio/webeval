"""Tests for admin-embedded stats, CSV exports, chart endpoint, and the
purge_experiment management command — all reachable via the Django admin
(the dashboard app was dissolved)."""
from __future__ import annotations

import csv
import io
import json

import pytest
from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from experiments.models import Experiment, Question
from experiments.tests.factories import (
    ChoiceQuestionFactory,
    ConditionFactory,
    ExperimentFactory,
    RatingQuestionFactory,
    StimulusFactory,
)
from survey.models import ParticipantSession, Response, StimulusAssignment

pytestmark = pytest.mark.django_db


# --- fixtures --------------------------------------------------------------


@pytest.fixture
def staff_client(db):
    user = User.objects.create_user("admin", "a@e.org", "pw", is_staff=True)
    client = Client()
    client.force_login(user)
    return client


@pytest.fixture
def populated_experiment(db):
    exp = ExperimentFactory(slug="pop-study", name="Pop study")
    cond_a = ConditionFactory(experiment=exp, name="A")
    cond_b = ConditionFactory(experiment=exp, name="B")
    stim_a = StimulusFactory(condition=cond_a, title="stim-a")
    stim_b = StimulusFactory(condition=cond_b, title="stim-b")
    q_rating = RatingQuestionFactory(experiment=exp, prompt="Quality?")
    q_demo = ChoiceQuestionFactory(
        experiment=exp,
        prompt="Age bracket",
        config={"choices": ["<25", "25-40", ">40"], "multi": False},
    )
    exp.state = Experiment.State.ACTIVE
    exp.save(update_fields=["state"])

    # Three completed sessions and one abandoned session.
    completed_sessions = []
    for i, (rating_a, rating_b, age) in enumerate(
        [(80, 40, "<25"), (60, 50, "25-40"), (70, 30, ">40")]
    ):
        session = ParticipantSession.objects.create(
            experiment=exp,
            last_step=ParticipantSession.Step.DONE,
            consented_at=timezone.now(),
            submitted_at=timezone.now(),
            device_type="desktop",
            browser_family="Firefox",
            country_code="IT",
        )
        assign_a = StimulusAssignment.objects.create(
            session=session, stimulus=stim_a, sort_order=0, listen_duration_ms=20000
        )
        assign_b = StimulusAssignment.objects.create(
            session=session, stimulus=stim_b, sort_order=1, listen_duration_ms=18500
        )
        Response.objects.create(
            session=session,
            stimulus=stim_a,
            question=q_rating,
            answer_value=json.dumps(rating_a),
        )
        Response.objects.create(
            session=session,
            stimulus=stim_b,
            question=q_rating,
            answer_value=json.dumps(rating_b),
        )
        Response.objects.create(
            session=session,
            stimulus=None,
            question=q_demo,
            answer_value=json.dumps(age),
        )
        completed_sessions.append(session)

    # One abandoned session (no submitted_at).
    ParticipantSession.objects.create(
        experiment=exp,
        last_step=ParticipantSession.Step.STIMULI,
        consented_at=timezone.now(),
        device_type="mobile",
        browser_family="Safari",
    )

    return {
        "experiment": exp,
        "cond_a": cond_a,
        "cond_b": cond_b,
        "stim_a": stim_a,
        "stim_b": stim_b,
        "q_rating": q_rating,
        "q_demo": q_demo,
        "sessions": completed_sessions,
    }


# --- staff gating ----------------------------------------------------------


class TestStaffGating:
    def test_anonymous_cannot_see_experiment_details(self, populated_experiment):
        exp = populated_experiment["experiment"]
        client = Client()
        response = client.get(
            reverse("admin:experiments_experiment_details", kwargs={"slug": exp.slug})
        )
        # admin_view redirects to /admin/login/ with next=...
        assert response.status_code in (302, 403)

    def test_staff_sees_admin_summary_cards(self, staff_client, populated_experiment):
        response = staff_client.get(reverse("admin:index"))
        assert response.status_code == 200
        body = response.content.decode()
        # Summary section is injected by core.context_processors.admin_summary.
        assert "webeval-summary" in body or "At a glance" in body
        assert "Experiments" in body
        assert "Sessions" in body


# --- summary helper --------------------------------------------------------


class TestGlobalSummary:
    def test_global_summary_counts(self, populated_experiment):
        from experiments.stats import global_summary

        # One extra draft experiment, to exercise the drafts counter.
        ExperimentFactory(slug="another-draft")
        summary = global_summary()
        # populated_experiment ships an active one; we added a draft.
        assert summary.total_experiments == 2
        assert summary.active == 1
        assert summary.drafts == 1
        assert summary.closed == 0
        # 3 completed + 1 abandoned session from the populated fixture.
        assert summary.total_sessions == 4
        assert summary.completed_sessions == 3
        # 3 sessions × (2 stimulus + 1 demographic answers) = 9.
        assert summary.total_responses == 9
        assert summary.completion_rate == pytest.approx(0.75)


# --- context processor ------------------------------------------------------


class TestAdminSummaryContextProcessor:
    def test_admin_summary_injected_for_staff_on_admin(self, staff_client):
        response = staff_client.get(reverse("admin:index"))
        assert response.status_code == 200
        assert "webeval_summary" in response.context
        summary = response.context["webeval_summary"]
        assert summary is not None
        assert hasattr(summary, "total_experiments")

    def test_admin_summary_absent_outside_admin(self, staff_client):
        from core.context_processors import admin_summary

        class _Req:
            path = "/s/some-slug/"
            user = None

        assert admin_summary(_Req()) == {}


# --- details page aggregation ---------------------------------------------


class TestDetailsPage:
    def test_details_show_completion_and_dropoff(self, staff_client, populated_experiment):
        exp = populated_experiment["experiment"]
        response = staff_client.get(
            reverse("admin:experiments_experiment_details", kwargs={"slug": exp.slug})
        )
        assert response.status_code == 200
        body = response.content.decode()
        # 3 completed + 1 abandoned = 4 started, 3 submitted.
        assert "3" in body  # completed count
        assert "4" in body  # total sessions
        assert "Pop study" in body

    def test_per_stimulus_mean_rating_matches(
        self, staff_client, populated_experiment
    ):
        exp = populated_experiment["experiment"]
        # Hand-computed means: stim-a = (80+60+70)/3 = 70, stim-b = (40+50+30)/3 = 40.
        from experiments.stats import per_stimulus_mean_ratings

        means = per_stimulus_mean_ratings(exp)
        by_title = {entry["title"]: entry for entry in means}
        assert by_title["stim-a"]["mean"] == pytest.approx(70.0)
        assert by_title["stim-b"]["mean"] == pytest.approx(40.0)


# --- CSV exports -----------------------------------------------------------


class TestCsvExports:
    def test_long_format_answers_csv(self, staff_client, populated_experiment):
        exp = populated_experiment["experiment"]
        response = staff_client.get(
            reverse("admin:experiments_experiment_answers_csv", kwargs={"slug": exp.slug})
        )
        assert response.status_code == 200
        assert response["Content-Type"].startswith("text/csv")
        reader = csv.DictReader(io.StringIO(response.content.decode()))
        rows = list(reader)
        # Exclude demographic answers (stimulus_id is NULL for those).
        assert reader.fieldnames == [
            "session_id",
            "submitted_at",
            "experiment",
            "stimulus_id",
            "condition",
            "question_id",
            "question_type",
            "answer_value",
            "listen_duration_ms",
        ]
        # 3 sessions × 2 stimulus-questions = 6 rows.
        assert len(rows) == 6
        first = rows[0]
        assert first["experiment"] == exp.slug
        assert first["question_type"] == "rating"
        assert int(first["listen_duration_ms"]) > 0

    def test_long_csv_excludes_abandoned_sessions(
        self, staff_client, populated_experiment
    ):
        exp = populated_experiment["experiment"]
        response = staff_client.get(
            reverse("admin:experiments_experiment_answers_csv", kwargs={"slug": exp.slug})
        )
        rows = list(csv.DictReader(io.StringIO(response.content.decode())))
        session_ids = {row["session_id"] for row in rows}
        # Only the 3 completed sessions should appear.
        assert len(session_ids) == 3

    def test_demographics_csv_one_row_per_completed_session(
        self, staff_client, populated_experiment
    ):
        exp = populated_experiment["experiment"]
        response = staff_client.get(
            reverse("admin:experiments_experiment_demographics_csv", kwargs={"slug": exp.slug})
        )
        assert response.status_code == 200
        rows = list(csv.DictReader(io.StringIO(response.content.decode())))
        assert len(rows) == 3
        assert "device_type" in rows[0]
        assert "country_code" in rows[0]
        # The demographic question should have its own column.
        demo_cols = [c for c in rows[0].keys() if c.startswith("q_")]
        assert demo_cols, "expected at least one q_<id> column"


# --- chart endpoint --------------------------------------------------------


class TestChartEndpoint:
    def test_mean_ratings_chart_returns_svg(
        self, staff_client, populated_experiment
    ):
        exp = populated_experiment["experiment"]
        response = staff_client.get(
            reverse(
                "admin:experiments_experiment_chart_mean_ratings",
                kwargs={"slug": exp.slug},
            )
        )
        assert response.status_code == 200
        assert response["Content-Type"].startswith("image/svg")
        body = response.content.decode()
        assert body.lstrip().startswith("<?xml") or body.lstrip().startswith("<svg")


# --- purge command ---------------------------------------------------------


class TestPurgeExperimentCommand:
    def test_purge_deletes_responses_assignments_sessions(
        self, populated_experiment
    ):
        exp = populated_experiment["experiment"]
        # Sanity: some data exists.
        assert ParticipantSession.objects.filter(experiment=exp).exists()
        assert Response.objects.filter(session__experiment=exp).exists()
        call_command("purge_experiment", exp.slug, "--yes")
        assert not ParticipantSession.objects.filter(experiment=exp).exists()
        assert not Response.objects.filter(session__experiment=exp).exists()
        assert not StimulusAssignment.objects.filter(
            stimulus__condition__experiment=exp
        ).exists()
        # The experiment itself and its stimuli/conditions/questions remain.
        assert Experiment.objects.filter(pk=exp.pk).exists()
        assert exp.conditions.exists()
        assert exp.questions.exists()

    def test_purge_leaves_other_experiments_alone(self, populated_experiment):
        exp = populated_experiment["experiment"]
        other = ExperimentFactory(slug="other-study")
        ConditionFactory(experiment=other, name="X")
        call_command("purge_experiment", exp.slug, "--yes")
        assert Experiment.objects.filter(pk=other.pk).exists()

    def test_purge_requires_yes_flag_or_prompt(self, populated_experiment):
        exp = populated_experiment["experiment"]
        with pytest.raises(SystemExit):
            call_command("purge_experiment", exp.slug)
        # Data still present.
        assert ParticipantSession.objects.filter(experiment=exp).exists()
