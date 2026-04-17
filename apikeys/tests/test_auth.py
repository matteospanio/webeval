from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth.models import User
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apikeys.models import APIKey, APIKeyEvent
from experiments.tests.factories import ExperimentFactory

pytestmark = pytest.mark.django_db


def _user(username="u", is_staff=True) -> User:
    return User.objects.create_user(username, f"{username}@e.org", "pw", is_staff=is_staff)


def _key(user, scopes=("stimuli:upload",), **kw):
    return APIKey.generate(user=user, name="t", scopes=list(scopes), **kw)


def _upload_url():
    exp = ExperimentFactory()
    return reverse("api_stimulus_upload", kwargs={"slug": exp.slug})


def test_no_header_returns_401_no_event():
    url = _upload_url()
    res = APIClient().post(url, {}, format="multipart")
    assert res.status_code == 401
    assert APIKeyEvent.objects.count() == 0


def test_malformed_header_logs_auth_failed():
    url = _upload_url()
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION="Token")
    res = client.post(url, {}, format="multipart")
    assert res.status_code == 401
    ev = APIKeyEvent.objects.get()
    assert ev.event_type == APIKeyEvent.Event.AUTH_FAILED
    assert ev.detail == {"reason": "malformed"}


def test_unknown_key_logs_auth_failed():
    url = _upload_url()
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION="Token webeval_nonsense")
    res = client.post(url, {}, format="multipart")
    assert res.status_code == 401
    ev = APIKeyEvent.objects.get()
    assert ev.detail == {"reason": "unknown"}
    assert ev.api_key is None


def test_revoked_key_rejected_and_logged():
    user = _user()
    key, raw = _key(user)
    key.revoked_at = timezone.now()
    key.save(update_fields=["revoked_at"])
    url = _upload_url()
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Token {raw}")
    res = client.post(url, {}, format="multipart")
    assert res.status_code == 401
    ev = APIKeyEvent.objects.filter(event_type=APIKeyEvent.Event.AUTH_FAILED).get()
    assert ev.detail == {"reason": "revoked"}
    assert ev.api_key_id == key.id


def test_expired_key_rejected_and_logged():
    user = _user()
    key, raw = _key(user, expires_at=timezone.now() - timedelta(seconds=1))
    url = _upload_url()
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Token {raw}")
    res = client.post(url, {}, format="multipart")
    assert res.status_code == 401
    ev = APIKeyEvent.objects.filter(event_type=APIKeyEvent.Event.AUTH_FAILED).get()
    assert ev.detail == {"reason": "expired"}


def test_non_staff_owner_rejected_and_logged():
    user = _user(is_staff=False)
    _, raw = _key(user)
    url = _upload_url()
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Token {raw}")
    res = client.post(url, {}, format="multipart")
    assert res.status_code == 401
    ev = APIKeyEvent.objects.filter(event_type=APIKeyEvent.Event.AUTH_FAILED).get()
    assert ev.detail == {"reason": "user_not_staff"}


def test_other_scheme_is_ignored():
    url = _upload_url()
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION="Bearer whatever")
    res = client.post(url, {}, format="multipart")
    # BaseAuthentication returns None → anonymous → 401 from perm, no event
    assert res.status_code == 401
    assert APIKeyEvent.objects.count() == 0
