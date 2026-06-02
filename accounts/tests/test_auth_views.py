"""Tests for registration + invitation-acceptance views."""
from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from accounts import services
from accounts.models import Membership
from accounts.roles import Role
from accounts.tests.factories import UserFactory
from experiments.tests.factories import ExperimentFactory

pytestmark = pytest.mark.django_db


def test_register_creates_user_with_profile_and_logs_in(client):
    resp = client.post(
        reverse("accounts:register"),
        {
            "username": "newbie",
            "email": "newbie@e.org",
            "password1": "super-secret-pw-1",
            "password2": "super-secret-pw-1",
        },
    )
    assert resp.status_code == 302
    user = User.objects.get(username="newbie")
    assert user.email == "newbie@e.org"
    assert hasattr(user, "profile")
    # logged in → studies page reachable
    assert client.get(reverse("studio:studies")).status_code == 200


def test_register_disabled_returns_404(settings, client):
    settings.ACCOUNTS_ALLOW_REGISTRATION = False
    assert client.get(reverse("accounts:register")).status_code == 404


def test_invite_accept_flow():
    owner = UserFactory()
    invitee = UserFactory()
    exp = ExperimentFactory(owner=owner)
    inv = services.invite_member(exp, invitee.email, Role.EDITOR, actor=owner)
    url = reverse("accounts:invite_accept", kwargs={"token": inv.token})

    # Anonymous → bounced to login carrying the invite as ?next=
    anon = Client()
    bounced = anon.get(url)
    assert bounced.status_code == 302
    assert "/accounts/login/" in bounced.url
    assert "next=" in bounced.url

    # Logged-in invitee: GET shows confirmation, POST accepts.
    c = Client()
    c.force_login(invitee)
    assert c.get(url).status_code == 200
    accepted = c.post(url)
    assert accepted.status_code == 302
    assert Membership.objects.filter(
        user=invitee, experiment=exp, role=Role.EDITOR
    ).exists()


def test_auth_pages_render(client):
    assert client.get(reverse("accounts:login")).status_code == 200
    assert client.get(reverse("accounts:register")).status_code == 200


def test_profile_page_renders_and_logout_works():
    user = UserFactory()
    c = Client()
    c.force_login(user)
    assert c.get(reverse("accounts:profile")).status_code == 200
    logout = c.post(reverse("accounts:logout"))
    assert logout.status_code == 302


def test_invite_accept_expired_token_410():
    from datetime import timedelta

    from django.utils import timezone

    owner = UserFactory()
    invitee = UserFactory()
    exp = ExperimentFactory(owner=owner)
    inv = services.invite_member(exp, invitee.email, Role.EDITOR, actor=owner)
    inv.expires_at = timezone.now() - timedelta(days=1)
    inv.save(update_fields=["expires_at"])

    c = Client()
    c.force_login(invitee)
    resp = c.get(reverse("accounts:invite_accept", kwargs={"token": inv.token}))
    assert resp.status_code == 410
    assert not Membership.objects.filter(user=invitee, experiment=exp).exists()
