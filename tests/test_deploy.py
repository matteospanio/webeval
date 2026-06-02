"""Deployment surface: the health-check endpoint (Epic 8)."""
from __future__ import annotations

import json

import pytest
from django.test import Client
from django.urls import reverse

pytestmark = pytest.mark.django_db


def test_healthz_ok_and_unauthenticated():
    resp = Client().get(reverse("healthz"))
    assert resp.status_code == 200
    data = json.loads(resp.content)
    assert data["status"] == "ok"
    assert data["database"] is True


def test_healthz_is_get_only():
    assert Client().post(reverse("healthz")).status_code == 405
