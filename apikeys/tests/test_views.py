from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from apikeys.models import APIKey, APIKeyEvent

pytestmark = pytest.mark.django_db


def _staff(username="u", is_superuser=False) -> User:
    return User.objects.create_user(
        username, f"{username}@e.org", "pw",
        is_staff=True, is_superuser=is_superuser,
    )


def _login(user) -> Client:
    c = Client()
    c.force_login(user)
    return c


def test_list_shows_only_own_keys():
    alice, bob = _staff("alice"), _staff("bob")
    APIKey.generate(user=alice, name="a-key", scopes=[])
    APIKey.generate(user=bob, name="b-key", scopes=[])
    res = _login(alice).get(reverse("apikeys:list"))
    body = res.content.decode()
    assert "a-key" in body and "b-key" not in body


def test_superuser_can_view_all_keys():
    admin = _staff("admin", is_superuser=True)
    other = _staff("other")
    APIKey.generate(user=other, name="other-key", scopes=[])
    res = _login(admin).get(reverse("apikeys:list") + "?scope=all")
    assert "other-key" in res.content.decode()


def test_create_redirects_to_show_key_and_shows_raw_once():
    alice = _staff("alice")
    c = _login(alice)
    res = c.post(
        reverse("apikeys:create"),
        {"name": "ci-bot", "scopes": ["stimuli:upload"]},
    )
    assert res.status_code == 302
    key = APIKey.objects.get()
    assert key.user == alice
    assert key.scopes == ["stimuli:upload"]
    assert APIKeyEvent.objects.filter(
        api_key=key, event_type=APIKeyEvent.Event.CREATED
    ).exists()

    # First GET reveals the raw key
    show_url = reverse("apikeys:show_key", args=[key.pk])
    res = c.get(show_url)
    assert res.status_code == 200
    body = res.content.decode()
    assert "webeval_" in body

    # Second GET does not
    res = c.get(show_url)
    assert res.status_code == 302  # redirect to list with warning


def test_rotate_creates_new_and_revokes_old():
    alice = _staff("alice")
    old, _ = APIKey.generate(user=alice, name="k", scopes=["stimuli:upload"])
    c = _login(alice)
    res = c.post(reverse("apikeys:rotate", args=[old.pk]))
    assert res.status_code == 302
    old.refresh_from_db()
    assert old.revoked_at is not None
    new = APIKey.objects.exclude(pk=old.pk).get()
    assert new.name == "k"
    assert new.scopes == ["stimuli:upload"]
    # Rotation event on old, creation event on new
    assert APIKeyEvent.objects.filter(
        api_key=old, event_type=APIKeyEvent.Event.ROTATED
    ).exists()
    ev = APIKeyEvent.objects.filter(
        api_key=new, event_type=APIKeyEvent.Event.CREATED
    ).get()
    assert ev.detail.get("rotated_from") == str(old.pk)


def test_revoke_stamps_timestamp_and_logs():
    alice = _staff("alice")
    key, _ = APIKey.generate(user=alice, name="k", scopes=[])
    c = _login(alice)
    res = c.post(reverse("apikeys:revoke", args=[key.pk]))
    assert res.status_code == 302
    key.refresh_from_db()
    assert key.revoked_at is not None
    assert APIKeyEvent.objects.filter(
        api_key=key, event_type=APIKeyEvent.Event.REVOKED
    ).exists()


def test_cross_user_access_404s():
    alice, bob = _staff("alice"), _staff("bob")
    bob_key, _ = APIKey.generate(user=bob, name="b", scopes=[])
    c = _login(alice)
    assert c.get(reverse("apikeys:events", args=[bob_key.pk])).status_code == 404
    assert c.post(reverse("apikeys:revoke", args=[bob_key.pk])).status_code == 404


def test_superuser_can_revoke_any_key():
    admin = _staff("admin", is_superuser=True)
    other = _staff("other")
    key, _ = APIKey.generate(user=other, name="k", scopes=[])
    c = _login(admin)
    res = c.post(reverse("apikeys:revoke", args=[key.pk]))
    assert res.status_code == 302
    key.refresh_from_db()
    assert key.revoked_at is not None


def test_anonymous_cannot_access():
    res = Client().get(reverse("apikeys:list"))
    assert res.status_code in (302, 403)  # staff_member_required → login redirect
