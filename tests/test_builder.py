"""Drag-&-drop question builder: the save endpoint contract (server side)."""
from __future__ import annotations

import json

import pytest
from django.test import Client
from django.urls import reverse

from accounts.roles import Role
from accounts.tests.factories import MembershipFactory, UserFactory
from experiments.models import Experiment, Question
from experiments.tests.factories import (
    ExperimentFactory,
    RatingQuestionFactory,
    TextQuestionFactory,
)

pytestmark = pytest.mark.django_db


def _owner_client(**exp_kwargs):
    owner = UserFactory()
    exp = ExperimentFactory(owner=owner, **exp_kwargs)
    client = Client()
    client.force_login(owner)
    return client, exp, owner


def _save(client, exp, questions):
    return client.post(
        reverse("studio:study_build_save", kwargs={"slug": exp.slug}),
        json.dumps({"questions": questions}),
        content_type="application/json",
    )


def test_build_save_creates_questions_in_order():
    client, exp, _ = _owner_client()
    resp = _save(
        client,
        exp,
        [
            {"type": "rating", "section": "stimulus", "prompt": "Quality?",
             "required": True, "config": {"min": 0, "max": 10, "step": 1}},
            {"type": "text", "section": "demographic", "prompt": "Comments",
             "required": False, "config": {"max_length": 200}},
        ],
    )
    assert resp.status_code == 200 and resp.json()["ok"]
    qs = list(exp.questions.order_by("sort_order"))
    assert [q.type for q in qs] == ["rating", "text"]
    assert qs[0].config == {"min": 0, "max": 10, "step": 1}
    assert [q.sort_order for q in qs] == [0, 1]


def test_build_save_updates_and_reorders():
    client, exp, _ = _owner_client()
    q1 = RatingQuestionFactory(experiment=exp, prompt="A", sort_order=0)
    q2 = TextQuestionFactory(experiment=exp, prompt="B", sort_order=1)
    resp = _save(
        client,
        exp,
        [
            {"id": q2.pk, "type": "text", "section": q2.section, "prompt": "B",
             "config": q2.config},
            {"id": q1.pk, "type": "rating", "section": q1.section, "prompt": "A2",
             "config": q1.config},
        ],
    )
    assert resp.status_code == 200
    q1.refresh_from_db()
    q2.refresh_from_db()
    assert q2.sort_order == 0 and q1.sort_order == 1
    assert q1.prompt == "A2"
    assert exp.questions.count() == 2


def test_build_save_deletes_removed_questions():
    client, exp, _ = _owner_client()
    q1 = RatingQuestionFactory(experiment=exp, sort_order=0)
    q2 = TextQuestionFactory(experiment=exp, sort_order=1)
    resp = _save(
        client,
        exp,
        [{"id": q1.pk, "type": "rating", "section": q1.section,
          "prompt": q1.prompt, "config": q1.config}],
    )
    assert resp.status_code == 200
    assert exp.questions.count() == 1
    assert not Question.objects.filter(pk=q2.pk).exists()


def test_build_save_rejects_bad_config_and_rolls_back():
    client, exp, _ = _owner_client()
    RatingQuestionFactory(experiment=exp, sort_order=0)  # pre-existing
    resp = _save(
        client,
        exp,
        [{"type": "rating", "section": "stimulus", "prompt": "Bad", "config": {}}],
    )
    assert resp.status_code == 400
    assert "0" in resp.json()["errors"]
    # Rolled back: the pre-existing question is untouched, nothing created.
    assert exp.questions.count() == 1


def test_build_save_supports_plugin_type():
    client, exp, _ = _owner_client()
    resp = _save(
        client,
        exp,
        [{"type": "constant_sum", "section": "stimulus", "prompt": "Split",
          "config": {"items": ["A", "B"], "total": 100}}],
    )
    assert resp.status_code == 200
    assert exp.questions.get().type == "constant_sum"


def test_build_save_blocked_when_not_draft():
    client, exp, _ = _owner_client(state=Experiment.State.ACTIVE)
    resp = _save(
        client,
        exp,
        [{"type": "text", "section": "demographic", "prompt": "x",
          "config": {"max_length": 10}}],
    )
    assert resp.status_code == 409


def test_build_save_forbidden_for_viewer():
    _, exp, _ = _owner_client()
    viewer = UserFactory()
    MembershipFactory(experiment=exp, user=viewer, role=Role.VIEWER)
    client = Client()
    client.force_login(viewer)
    resp = _save(
        client, exp,
        [{"type": "text", "section": "demographic", "prompt": "x",
          "config": {"max_length": 10}}],
    )
    assert resp.status_code == 403


def test_build_page_renders_palette_and_questions():
    client, exp, _ = _owner_client()
    RatingQuestionFactory(experiment=exp, prompt="Existing Q")
    body = client.get(
        reverse("studio:study_build", kwargs={"slug": exp.slug})
    ).content.decode()
    assert "constant_sum" in body  # plugin component offered in the palette
    assert "Existing Q" in body  # current questions hydrated
