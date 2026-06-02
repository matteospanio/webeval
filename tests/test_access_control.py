"""Cross-cutting access control: admin scoping, API object permission, and an
end-to-end "owner invites editor who can then edit" flow over owned studies."""
from __future__ import annotations

import pytest
from django.contrib.auth.models import Permission
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse
from rest_framework.test import APIClient

from accounts import services
from accounts.models import Membership
from accounts.roles import Role
from accounts.tests.factories import StaffUserFactory, UserFactory
from apikeys.models import APIKey
from experiments.models import Stimulus
from experiments.tests.factories import ConditionFactory, ExperimentFactory

pytestmark = pytest.mark.django_db

MP3 = b"ID3\x03\x00\x00\x00\x00\x00\x00data"


def _audio():
    return SimpleUploadedFile("c.mp3", MP3, content_type="audio/mpeg")


def _key_client(user, scopes=("stimuli:upload",)):
    _, raw = APIKey.generate(user=user, name="k", scopes=list(scopes))
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Token {raw}")
    return client


def test_admin_changelist_scoped_to_owned_experiments():
    a = StaffUserFactory()
    b = StaffUserFactory()
    perms = Permission.objects.filter(
        content_type__app_label="experiments", codename="view_experiment"
    )
    a.user_permissions.add(*perms)
    ExperimentFactory(owner=a, name="A-owned-study")
    ExperimentFactory(owner=b, name="B-owned-study")

    client = Client()
    client.force_login(a)
    resp = client.get(reverse("admin:experiments_experiment_changelist"))
    assert b"A-owned-study" in resp.content
    assert b"B-owned-study" not in resp.content


def test_admin_details_view_403_for_non_owner_staff():
    a = StaffUserFactory()
    b = StaffUserFactory()
    exp_b = ExperimentFactory(owner=b)
    client = Client()
    client.force_login(a)
    url = reverse(
        "admin:experiments_experiment_details", kwargs={"slug": exp_b.slug}
    )
    assert client.get(url).status_code == 403


def test_api_upload_denied_for_stranger_allowed_for_owner():
    owner = StaffUserFactory()
    stranger = StaffUserFactory()
    exp = ExperimentFactory(owner=owner)  # draft
    ConditionFactory(experiment=exp, name="A")
    url = reverse("api_stimulus_upload", kwargs={"slug": exp.slug})

    denied = _key_client(stranger).post(
        url,
        {"condition": "A", "title": "t", "kind": Stimulus.Kind.AUDIO, "audio": _audio()},
        format="multipart",
    )
    assert denied.status_code == 403

    allowed = _key_client(owner).post(
        url,
        {"condition": "A", "title": "t", "kind": Stimulus.Kind.AUDIO, "audio": _audio()},
        format="multipart",
    )
    assert allowed.status_code == 201, allowed.content


def test_e2e_owner_invites_editor_who_can_edit_via_api():
    owner = StaffUserFactory()
    editor = StaffUserFactory()
    exp = ExperimentFactory(owner=owner)  # draft
    ConditionFactory(experiment=exp, name="A")

    # Editor has no access yet → 403.
    url = reverse("api_stimulus_upload", kwargs={"slug": exp.slug})
    before = _key_client(editor).post(
        url,
        {"condition": "A", "title": "t", "kind": Stimulus.Kind.AUDIO, "audio": _audio()},
        format="multipart",
    )
    assert before.status_code == 403

    # Owner invites; editor accepts.
    inv = services.invite_member(exp, editor.email, Role.EDITOR, actor=owner)
    services.accept_invitation(inv, editor)
    assert Membership.objects.filter(
        user=editor, experiment=exp, role=Role.EDITOR
    ).exists()

    # Now the editor's key can upload to the draft study.
    after = _key_client(editor).post(
        url,
        {"condition": "A", "title": "t", "kind": Stimulus.Kind.AUDIO, "audio": _audio()},
        format="multipart",
    )
    assert after.status_code == 201, after.content
