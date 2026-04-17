"""Tests for the TEST experiment state: transitions, structural-edit lock,
survey runnability, test-mode banner, and the admin Activate confirmation
view that promotes TEST → ACTIVE with optional data reset."""
from __future__ import annotations

import json

import pytest
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from experiments.data_ops import purge_participant_data
from experiments.models import Experiment
from experiments.tests.factories import (
    ChoiceQuestionFactory,
    ConditionFactory,
    ExperimentFactory,
    RatingQuestionFactory,
    StimulusFactory,
    TextStimulusFactory,
)
from survey.models import ParticipantSession, Response, StimulusAssignment

pytestmark = pytest.mark.django_db


# --- State transitions -----------------------------------------------------


class TestStateTransitions:
    def test_draft_to_test_allowed(self):
        exp = ExperimentFactory()
        exp.state = Experiment.State.TEST
        exp.full_clean()
        exp.save()
        exp.refresh_from_db()
        assert exp.state == Experiment.State.TEST

    def test_test_to_draft_allowed(self):
        exp = ExperimentFactory()
        exp.state = Experiment.State.TEST
        exp.save(update_fields=["state"])
        exp.state = Experiment.State.DRAFT
        exp.full_clean()
        exp.save()
        assert exp.state == Experiment.State.DRAFT

    def test_direct_test_to_active_blocked_by_clean(self):
        exp = ExperimentFactory()
        exp.state = Experiment.State.TEST
        exp.save(update_fields=["state"])
        exp.state = Experiment.State.ACTIVE
        with pytest.raises(ValidationError) as excinfo:
            exp.full_clean()
        assert "Activate button" in str(excinfo.value)

    def test_test_to_active_succeeds_with_confirmation_flag(self):
        exp = ExperimentFactory()
        exp.state = Experiment.State.TEST
        exp.save(update_fields=["state"])
        exp.state = Experiment.State.ACTIVE
        exp._activate_confirmed = True
        exp.full_clean()
        exp.save()
        exp.refresh_from_db()
        assert exp.state == Experiment.State.ACTIVE

    def test_test_to_closed_allowed(self):
        exp = ExperimentFactory()
        exp.state = Experiment.State.TEST
        exp.save(update_fields=["state"])
        exp.state = Experiment.State.CLOSED
        exp.full_clean()
        exp.save()
        assert exp.state == Experiment.State.CLOSED


# --- Structural-edit lock --------------------------------------------------


class TestStructuralLockInTestState:
    def test_cannot_add_condition_to_test_experiment(self):
        exp = ExperimentFactory(state=Experiment.State.TEST)
        cond = ConditionFactory.build(experiment=exp)
        with pytest.raises(ValidationError):
            cond.full_clean()

    def test_cannot_add_question_to_test_experiment(self):
        exp = ExperimentFactory(state=Experiment.State.TEST)
        q = RatingQuestionFactory.build(experiment=exp)
        with pytest.raises(ValidationError):
            q.full_clean()

    def test_cannot_delete_question_from_test_experiment(self):
        exp = ExperimentFactory()
        q = RatingQuestionFactory(experiment=exp)
        exp.state = Experiment.State.TEST
        exp.save(update_fields=["state"])
        with pytest.raises(ValidationError):
            q.delete()


# --- Survey runnability + banner -------------------------------------------


def _make_runnable_experiment(state: str) -> Experiment:
    """Build a minimal but complete experiment and flip it to ``state``."""
    exp = ExperimentFactory()
    cond = ConditionFactory(experiment=exp)
    TextStimulusFactory(condition=cond)
    ChoiceQuestionFactory(
        experiment=exp,
        prompt="Quality?",
        config={"choices": ["good", "bad"], "multi": False},
    )
    exp.state = state
    exp.save(update_fields=["state"])
    return exp


class TestSurveyRunnableInTestState:
    def test_consent_page_renders_in_test_state(self, client):
        exp = _make_runnable_experiment(Experiment.State.TEST)
        response = client.get(reverse("survey:consent", kwargs={"slug": exp.slug}))
        assert response.status_code == 200
        body = response.content.decode()
        # The real consent form should render, not the unavailable copy.
        assert "not currently accepting responses" not in body

    def test_test_mode_banner_rendered_in_test_state(self, client):
        exp = _make_runnable_experiment(Experiment.State.TEST)
        response = client.get(reverse("survey:consent", kwargs={"slug": exp.slug}))
        body = response.content.decode()
        assert "Test mode" in body
        assert "test-mode-banner" in body

    def test_no_banner_in_active_state(self, client):
        exp = _make_runnable_experiment(Experiment.State.ACTIVE)
        response = client.get(reverse("survey:consent", kwargs={"slug": exp.slug}))
        body = response.content.decode()
        assert "test-mode-banner" not in body

    def test_draft_experiment_still_unavailable(self, client):
        exp = ExperimentFactory(state=Experiment.State.DRAFT)
        response = client.get(reverse("survey:consent", kwargs={"slug": exp.slug}))
        assert response.status_code == 200
        body = response.content.decode()
        assert "not currently accepting responses" in body

    def test_closed_experiment_still_unavailable(self, client):
        exp = _make_runnable_experiment(Experiment.State.ACTIVE)
        exp.state = Experiment.State.CLOSED
        exp.save(update_fields=["state"])
        response = client.get(reverse("survey:consent", kwargs={"slug": exp.slug}))
        body = response.content.decode()
        assert "not currently accepting responses" in body


# --- Purge helper ----------------------------------------------------------


@pytest.fixture
def test_phase_experiment(db):
    exp = ExperimentFactory(state=Experiment.State.TEST, slug="under-test")
    cond = ConditionFactory(experiment=exp, name="A")
    stim = StimulusFactory(condition=cond, title="s1")
    q = RatingQuestionFactory(experiment=exp, prompt="Quality?")
    for _ in range(2):
        session = ParticipantSession.objects.create(
            experiment=exp,
            last_step=ParticipantSession.Step.DONE,
            consented_at=timezone.now(),
            submitted_at=timezone.now(),
        )
        StimulusAssignment.objects.create(
            session=session, stimulus=stim, sort_order=0, listen_duration_ms=1000
        )
        Response.objects.create(
            session=session, stimulus=stim, question=q, answer_value=json.dumps(50)
        )
    return exp


class TestPurgeHelper:
    def test_returns_counts_and_deletes(self, test_phase_experiment):
        exp = test_phase_experiment
        counts = purge_participant_data(exp)
        assert counts.sessions == 2
        assert counts.assignments == 2
        assert counts.responses == 2
        assert not ParticipantSession.objects.filter(experiment=exp).exists()
        assert not Response.objects.filter(session__experiment=exp).exists()
        # Config survives.
        assert exp.conditions.exists()
        assert exp.questions.exists()


# --- Activate view ---------------------------------------------------------


@pytest.fixture
def staff_client(db):
    user = User.objects.create_user("admin", "a@e.org", "pw", is_staff=True, is_superuser=True)
    client = Client()
    client.force_login(user)
    return client


class TestActivateView:
    def test_get_shows_counts(self, staff_client, test_phase_experiment):
        exp = test_phase_experiment
        url = reverse("admin:experiments_experiment_activate", kwargs={"slug": exp.slug})
        response = staff_client.get(url)
        assert response.status_code == 200
        body = response.content.decode()
        assert "Activate" in body
        assert "Reset participant data" in body
        # Both sessions should be reflected.
        assert "2" in body

    def test_post_with_purge_wipes_and_activates(
        self, staff_client, test_phase_experiment
    ):
        exp = test_phase_experiment
        url = reverse("admin:experiments_experiment_activate", kwargs={"slug": exp.slug})
        response = staff_client.post(url, {"purge": "on"})
        assert response.status_code == 302
        exp.refresh_from_db()
        assert exp.state == Experiment.State.ACTIVE
        assert not ParticipantSession.objects.filter(experiment=exp).exists()
        assert not Response.objects.filter(session__experiment=exp).exists()

    def test_post_without_purge_keeps_data_and_activates(
        self, staff_client, test_phase_experiment
    ):
        exp = test_phase_experiment
        url = reverse("admin:experiments_experiment_activate", kwargs={"slug": exp.slug})
        response = staff_client.post(url, {})  # purge checkbox unchecked
        assert response.status_code == 302
        exp.refresh_from_db()
        assert exp.state == Experiment.State.ACTIVE
        assert ParticipantSession.objects.filter(experiment=exp).count() == 2
        assert Response.objects.filter(session__experiment=exp).count() == 2

    def test_anonymous_redirected_to_login(self, test_phase_experiment):
        exp = test_phase_experiment
        url = reverse("admin:experiments_experiment_activate", kwargs={"slug": exp.slug})
        response = Client().get(url)
        assert response.status_code in (302, 403)

    def test_non_test_experiment_redirects_to_change(
        self, staff_client, test_phase_experiment
    ):
        exp = test_phase_experiment
        exp.state = Experiment.State.DRAFT
        exp.save(update_fields=["state"])
        url = reverse("admin:experiments_experiment_activate", kwargs={"slug": exp.slug})
        response = staff_client.get(url)
        # Not the activate form — we bounce back to the change view.
        assert response.status_code == 302
        assert "activate" not in response["Location"]


# --- Global summary includes test count ------------------------------------


class TestGlobalSummaryIncludesTest:
    def test_test_count_in_summary(self):
        from experiments.stats import global_summary

        ExperimentFactory(slug="d")
        ExperimentFactory(slug="t", state=Experiment.State.TEST)
        ExperimentFactory(slug="a", state=Experiment.State.ACTIVE)
        summary = global_summary()
        assert summary.drafts == 1
        assert summary.test == 1
        assert summary.active == 1
