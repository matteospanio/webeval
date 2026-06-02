"""Question banks & reusable templates (Epic 5)."""
from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from experiments.models import Experiment, Question, QuestionTemplate
from experiments.tests.factories import ExperimentFactory, RatingQuestionFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def admin_user():
    user = User.objects.create_user(
        "bankadmin", "b@e.org", "pw", is_staff=True, is_superuser=True
    )
    client = Client()
    client.force_login(user)
    return client, user


def test_template_round_trip_between_experiments():
    exp1 = ExperimentFactory()
    q = RatingQuestionFactory(experiment=exp1, prompt="Q1")
    tpl = QuestionTemplate.from_question(q, owner=None)
    tpl.save()

    exp2 = ExperimentFactory()
    new_q = tpl.build_question(exp2, sort_order=5)
    new_q.save()
    assert new_q.experiment == exp2
    assert new_q.prompt == "Q1"
    assert new_q.type == q.type
    assert new_q.config == q.config
    assert new_q.sort_order == 5


def test_save_to_bank_admin_action(admin_user):
    client, user = admin_user
    exp = ExperimentFactory()
    q = RatingQuestionFactory(experiment=exp, prompt="Quality?")
    client.post(
        reverse("admin:experiments_question_changelist"),
        {"action": "save_questions_to_bank", "_selected_action": [str(q.pk)]},
    )
    tpl = QuestionTemplate.objects.get(owner=user)
    assert tpl.prompt == "Quality?"
    assert tpl.type == Question.Type.RATING


def test_add_from_bank_view_inserts_into_draft(admin_user):
    client, user = admin_user
    exp = ExperimentFactory()  # draft
    tpl = QuestionTemplate.objects.create(
        owner=user,
        name="NPS",
        type=Question.Type.RATING,
        section=Question.Section.STIMULUS,
        prompt="Recommend?",
        config={"min": 0, "max": 10, "step": 1},
    )
    url = reverse(
        "admin:experiments_experiment_add_from_bank", kwargs={"slug": exp.slug}
    )
    assert "NPS" in client.get(url).content.decode()

    resp = client.post(url, {"template": [str(tpl.pk)]})
    assert resp.status_code == 302
    assert exp.questions.filter(prompt="Recommend?").exists()


def test_add_from_bank_blocked_when_not_draft(admin_user):
    client, user = admin_user
    exp = ExperimentFactory(state=Experiment.State.ACTIVE)
    tpl = QuestionTemplate.objects.create(
        owner=user,
        name="X",
        type=Question.Type.TEXT,
        prompt="late?",
        config={"max_length": 100},
    )
    url = reverse(
        "admin:experiments_experiment_add_from_bank", kwargs={"slug": exp.slug}
    )
    resp = client.post(url, {"template": [str(tpl.pk)]})
    assert resp.status_code == 302
    assert not exp.questions.filter(prompt="late?").exists()


def test_bank_view_hides_other_users_templates(admin_user):
    client, user = admin_user
    other = User.objects.create_user("other", "o@e.org", "pw")
    QuestionTemplate.objects.create(
        owner=other, name="Private", type=Question.Type.TEXT, prompt="hidden"
    )
    QuestionTemplate.objects.create(
        owner=None, name="Shared", type=Question.Type.TEXT, prompt="visible"
    )
    exp = ExperimentFactory()
    url = reverse(
        "admin:experiments_experiment_add_from_bank", kwargs={"slug": exp.slug}
    )
    body = client.get(url).content.decode()
    assert "Shared" in body
    assert "Private" not in body
