"""The landing page was removed in the direct-share refactor.

`/` now 404s; surveys are reached via `/s/<slug>/`. This module keeps a
couple of sanity smoke tests around the new behaviour and the admin
login, which used to be bundled with the landing tests.
"""
from __future__ import annotations

import pytest
from django.test import Client
from django.urls import NoReverseMatch, reverse


def test_landing_url_name_no_longer_resolves():
    with pytest.raises(NoReverseMatch):
        reverse("survey:landing")


@pytest.mark.django_db
def test_root_returns_404():
    client = Client()
    response = client.get("/")
    assert response.status_code == 404


@pytest.mark.django_db
def test_admin_reachable():
    """The admin login page should be reachable even with no users."""
    client = Client()
    response = client.get("/admin/login/")
    assert response.status_code == 200
