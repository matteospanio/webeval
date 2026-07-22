"""In-studio study lifecycle (test/activate/close/reopen/draft)."""
from __future__ import annotations

import pytest
from django.test import Client
from django.urls import reverse

from accounts.models import AuditEvent, Membership
from accounts.roles import Role
from accounts.tests.factories import UserFactory
from experiments.models import Experiment
from experiments.tests.factories import (
    ConditionFactory,
    ExperimentFactory,
    RatingQuestionFactory,
    TextStimulusFactory,
)
from survey.models import ParticipantSession

pytestmark = pytest.mark.django_db


def _ready_study(owner, state=Experiment.State.DRAFT, **kw):
    exp = ExperimentFactory(owner=owner, **kw)
    cond = ConditionFactory(experiment=exp)
    TextStimulusFactory(condition=cond)
    RatingQuestionFactory(experiment=exp)
    if state != Experiment.State.DRAFT:
        exp.state = state
        exp.save(update_fields=["state"])
    return exp


def _client(user):
    client = Client()
    client.force_login(user)
    return client


def _state_url(exp, action):
    return reverse(
        "studio:study_state_change", kwargs={"slug": exp.slug, "action": action}
    )


def test_editor_cannot_change_state():
    owner, editor = UserFactory(), UserFactory()
    exp = _ready_study(owner)
    Membership.objects.create(experiment=exp, user=editor, role=Role.EDITOR)
    resp = _client(editor).get(_state_url(exp, "test"))
    assert resp.status_code == 403


def test_unknown_action_is_404():
    owner = UserFactory()
    exp = _ready_study(owner)
    assert _client(owner).get(_state_url(exp, "explode")).status_code == 404


def test_inapplicable_action_redirects_with_warning():
    owner = UserFactory()
    exp = _ready_study(owner)  # draft — "reopen" doesn't apply
    resp = _client(owner).post(_state_url(exp, "reopen"), follow=True)
    exp.refresh_from_db()
    assert exp.state == Experiment.State.DRAFT
    assert "does not apply" in resp.content.decode()


def test_draft_to_test_and_activate_happy_path():
    owner = UserFactory()
    exp = _ready_study(owner)
    client = _client(owner)

    confirm = client.get(_state_url(exp, "test"))
    assert confirm.status_code == 200
    assert client.post(_state_url(exp, "test")).status_code == 302
    exp.refresh_from_db()
    assert exp.state == Experiment.State.TEST

    # TEST → ACTIVE shows the test-data choice on the confirm page.
    confirm = client.get(_state_url(exp, "activate"))
    assert 'name="test_data"' in confirm.content.decode()
    assert client.post(_state_url(exp, "activate"), {"test_data": "keep"}).status_code == 302
    exp.refresh_from_db()
    assert exp.state == Experiment.State.ACTIVE
    assert AuditEvent.objects.filter(
        experiment=exp, action=AuditEvent.Action.ACTIVATE
    ).exists()


def test_unwalkable_draft_cannot_start_testing():
    owner = UserFactory()
    exp = ExperimentFactory(owner=owner)  # empty: no conditions/stimuli/questions
    resp = _client(owner).post(_state_url(exp, "test"), follow=True)
    exp.refresh_from_db()
    assert exp.state == Experiment.State.DRAFT


def test_activate_blocked_by_readiness_problems():
    owner = UserFactory()
    exp = ExperimentFactory(owner=owner, consent_text="")  # not ready
    ConditionFactory(experiment=exp)
    resp = _client(owner).post(_state_url(exp, "activate"))
    exp.refresh_from_db()
    assert exp.state == Experiment.State.DRAFT


def test_activate_keep_promotes_preview_sessions():
    owner = UserFactory()
    exp = _ready_study(owner, state=Experiment.State.TEST)
    session = ParticipantSession.objects.create(experiment=exp, is_preview=True)
    _client(owner).post(_state_url(exp, "activate"), {"test_data": "keep"})
    session.refresh_from_db()
    exp.refresh_from_db()
    assert exp.state == Experiment.State.ACTIVE
    assert session.is_preview is False


def test_activate_purge_deletes_preview_sessions_and_audits():
    owner = UserFactory()
    exp = _ready_study(owner, state=Experiment.State.TEST)
    ParticipantSession.objects.create(experiment=exp, is_preview=True)
    _client(owner).post(_state_url(exp, "activate"), {"test_data": "purge"})
    exp.refresh_from_db()
    assert exp.state == Experiment.State.ACTIVE
    assert not ParticipantSession.objects.filter(experiment=exp).exists()
    actions = set(
        AuditEvent.objects.filter(experiment=exp).values_list("action", flat=True)
    )
    assert AuditEvent.Action.PURGE in actions
    assert AuditEvent.Action.ACTIVATE in actions


def test_close_reopen_and_back_to_draft_round_trip():
    owner = UserFactory()
    exp = _ready_study(owner, state=Experiment.State.ACTIVE)
    client = _client(owner)

    client.post(_state_url(exp, "close"))
    exp.refresh_from_db()
    assert exp.state == Experiment.State.CLOSED

    client.post(_state_url(exp, "reopen"))
    exp.refresh_from_db()
    assert exp.state == Experiment.State.ACTIVE

    # active → draft isn't offered as an action
    assert client.post(_state_url(exp, "draft"), follow=True).status_code == 200
    exp.refresh_from_db()
    assert exp.state == Experiment.State.ACTIVE

    # test → draft is
    exp.state = Experiment.State.TEST
    exp.save(update_fields=["state"])
    client.post(_state_url(exp, "draft"))
    exp.refresh_from_db()
    assert exp.state == Experiment.State.DRAFT
    assert AuditEvent.objects.filter(
        experiment=exp, action=AuditEvent.Action.EDIT, target="state"
    ).count() >= 1


def test_overview_shows_lifecycle_buttons_for_managers_only():
    owner, viewer = UserFactory(), UserFactory()
    exp = _ready_study(owner)
    Membership.objects.create(experiment=exp, user=viewer, role=Role.VIEWER)
    overview = reverse("studio:study_overview", kwargs={"slug": exp.slug})

    body = _client(owner).get(overview).content.decode()
    assert _state_url(exp, "test") in body

    body = _client(viewer).get(overview).content.decode()
    assert _state_url(exp, "test") not in body