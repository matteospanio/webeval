"""Tests for the studio dashboard views and their permission gates."""
from __future__ import annotations

import pytest
from django.test import Client
from django.urls import reverse

from accounts.models import Invitation, Membership
from accounts.roles import Role
from accounts.tests.factories import UserFactory
from experiments.models import Experiment
from experiments.tests.factories import ExperimentFactory

pytestmark = pytest.mark.django_db


def _client(user):
    c = Client()
    c.force_login(user)
    return c


def test_studies_list_shows_only_my_studies():
    me = UserFactory()
    other = UserFactory()
    mine = ExperimentFactory(owner=me, name="Mine-Study")
    ExperimentFactory(owner=other, name="Their-Study")

    resp = _client(me).get(reverse("studio:studies"))
    assert resp.status_code == 200
    assert b"Mine-Study" in resp.content
    assert b"Their-Study" not in resp.content


def test_create_study_sets_owner_and_owner_membership():
    me = UserFactory()
    resp = _client(me).post(
        reverse("studio:study_create"),
        {"name": "My New Study", "description": "", "mode": Experiment.Mode.STANDARD},
    )
    assert resp.status_code == 302
    exp = Experiment.objects.get(name="My New Study")
    assert exp.owner == me
    assert exp.slug
    assert Membership.objects.filter(
        user=me, experiment=exp, role=Role.OWNER
    ).exists()


def test_overview_visible_to_member_but_404_to_outsider():
    owner = UserFactory()
    member = UserFactory()
    outsider = UserFactory()
    exp = ExperimentFactory(owner=owner)
    Membership.objects.create(user=member, experiment=exp, role=Role.VIEWER)
    url = reverse("studio:study_overview", kwargs={"slug": exp.slug})
    assert _client(member).get(url).status_code == 200
    assert _client(outsider).get(url).status_code == 404


def test_access_page_is_owner_only():
    owner = UserFactory()
    editor = UserFactory()
    exp = ExperimentFactory(owner=owner)
    Membership.objects.create(user=editor, experiment=exp, role=Role.EDITOR)
    url = reverse("studio:study_access", kwargs={"slug": exp.slug})
    assert _client(owner).get(url).status_code == 200
    # editor may view the study but not manage access
    assert _client(editor).get(url).status_code == 403


def test_invite_through_access_page_creates_invitation():
    owner = UserFactory()
    exp = ExperimentFactory(owner=owner)
    resp = _client(owner).post(
        reverse("studio:study_access", kwargs={"slug": exp.slug}),
        {"action": "invite", "email": "x@e.org", "role": Role.EDITOR},
    )
    assert resp.status_code == 302
    assert Invitation.objects.filter(experiment=exp, email="x@e.org").exists()


def test_export_csv_is_view_gated():
    owner = UserFactory()
    outsider = UserFactory()
    exp = ExperimentFactory(owner=owner)
    url = reverse("studio:answers_csv", kwargs={"slug": exp.slug})
    assert _client(owner).get(url).status_code == 200
    assert _client(outsider).get(url).status_code == 404


def test_anonymous_redirected_to_login():
    resp = Client().get(reverse("studio:studies"))
    assert resp.status_code == 302
    assert "/accounts/login/" in resp.url


def test_create_form_renders():
    me = UserFactory()
    assert _client(me).get(reverse("studio:study_create")).status_code == 200
