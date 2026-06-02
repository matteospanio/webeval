"""Authoring a plugin question type through the Django admin."""
from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from experiments.models import Question
from experiments.tests.factories import ExperimentFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def admin_client():
    User.objects.create_user(
        "compadmin", "c@e.org", "pw", is_staff=True, is_superuser=True
    )
    client = Client()
    client.force_login(User.objects.get(username="compadmin"))
    return client


def _add_data(exp, plugin_config):
    return {
        "experiment": exp.pk,
        "section": Question.Section.STIMULUS,
        "type": "constant_sum",
        "prompt": "Allocate your budget",
        "sort_order": 0,
        "plugin_config": plugin_config,
    }


def test_admin_can_author_plugin_question(admin_client):
    exp = ExperimentFactory()  # draft
    url = reverse("admin:experiments_question_add")
    resp = admin_client.post(
        url, _add_data(exp, '{"items": ["A", "B"], "total": 100}')
    )
    assert resp.status_code == 302  # saved → redirect to changelist
    q = Question.objects.get(type="constant_sum")
    assert q.experiment == exp
    assert q.config == {"items": ["A", "B"], "total": 100}


def test_admin_rejects_bad_plugin_config(admin_client):
    exp = ExperimentFactory()
    url = reverse("admin:experiments_question_add")
    resp = admin_client.post(url, _add_data(exp, '{"items": ["only-one"]}'))
    assert resp.status_code == 200  # re-rendered with form errors
    assert not Question.objects.filter(type="constant_sum").exists()
