"""End-to-end audit trail: successful use bumps last_used_at + logs `used`;
scope-denied requests log `used` with a 403 response code (not auth_failed)."""
from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.urls import reverse
from rest_framework.test import APIClient

from apikeys.models import APIKey, APIKeyEvent
from experiments.tests.factories import ExperimentFactory

pytestmark = pytest.mark.django_db


def _staff() -> User:
    return User.objects.create_user("api", "a@e.org", "pw", is_staff=True)


def test_successful_call_logs_used_and_bumps_last_used():
    user = _staff()
    key, raw = APIKey.generate(user=user, name="k", scopes=["stimuli:upload"])
    exp = ExperimentFactory()
    url = reverse("api_stimulus_upload", kwargs={"slug": exp.slug})
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Token {raw}")
    # Missing fields → 400, but it still counts as a successful auth/use
    res = client.post(url, {}, format="multipart")
    assert res.status_code == 400

    ev = APIKeyEvent.objects.get(event_type=APIKeyEvent.Event.USED)
    assert ev.api_key_id == key.id
    assert ev.response_status == 400
    assert ev.request_method == "POST"
    assert ev.request_path.endswith(f"/experiments/{exp.slug}/stimuli/")

    key.refresh_from_db()
    assert key.last_used_at is not None


def test_scope_denied_logs_used_not_auth_failed():
    user = _staff()
    _, raw = APIKey.generate(
        user=user, name="k", scopes=["pairwise-answers:read"]
    )
    exp = ExperimentFactory()
    url = reverse("api_stimulus_upload", kwargs={"slug": exp.slug})
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Token {raw}")
    res = client.post(url, {}, format="multipart")
    assert res.status_code == 403

    # permission failure, not authentication failure
    assert not APIKeyEvent.objects.filter(
        event_type=APIKeyEvent.Event.AUTH_FAILED
    ).exists()
    ev = APIKeyEvent.objects.get(event_type=APIKeyEvent.Event.USED)
    assert ev.response_status == 403
