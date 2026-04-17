"""Tests for the staff-only database export endpoint."""
from __future__ import annotations

import json

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from experiments.models import Experiment
from experiments.tests.factories import ExperimentFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def staff_client(db):
    User.objects.create_user("admin", "a@e.org", "pw", is_staff=True)
    client = Client()
    client.login(username="admin", password="pw")
    return client


def test_database_export_requires_staff():
    client = Client()
    response = client.get(reverse("webeval_database_export"))
    # staff_member_required redirects anonymous/non-staff users to login.
    assert response.status_code in (302, 403)
    assert "/admin/login/" in response.get("Location", "")


def test_database_export_forbids_non_staff():
    User.objects.create_user("joe", "j@e.org", "pw", is_staff=False)
    client = Client()
    client.login(username="joe", password="pw")
    response = client.get(reverse("webeval_database_export"))
    assert response.status_code in (302, 403)


def test_database_export_streams_dumpdata_json(staff_client):
    ExperimentFactory(slug="export-me", name="Exportable")
    response = staff_client.get(reverse("webeval_database_export"))
    assert response.status_code == 200
    assert response["Content-Type"].startswith("application/json")
    disposition = response["Content-Disposition"]
    assert disposition.startswith('attachment; filename="webeval-db-')
    assert disposition.endswith('.json"')

    payload = json.loads(response.content)
    assert isinstance(payload, list)
    models = {row["model"] for row in payload}
    assert "experiments.experiment" in models
    # Noisy / non-portable tables are excluded.
    assert "contenttypes.contenttype" not in models
    assert "auth.permission" not in models
    assert "sessions.session" not in models
    assert "admin.logentry" not in models

    # The experiment we just created round-trips.
    experiments = [
        row for row in payload if row["model"] == "experiments.experiment"
    ]
    slugs = {row["fields"]["slug"] for row in experiments}
    assert "export-me" in slugs


def test_export_link_rendered_on_admin_index(staff_client):
    response = staff_client.get("/admin/")
    assert response.status_code == 200
    export_url = reverse("webeval_database_export")
    assert export_url.encode() in response.content
