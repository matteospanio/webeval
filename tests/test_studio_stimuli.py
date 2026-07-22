"""In-studio conditions & stimuli authoring."""
from __future__ import annotations

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse

from accounts.models import AuditEvent, Membership
from accounts.roles import Role
from accounts.tests.factories import UserFactory
from experiments.models import Condition, Experiment, Stimulus
from experiments.tests.factories import (
    ConditionFactory,
    ExperimentFactory,
    StimulusFactory,
    TextStimulusFactory,
)

pytestmark = pytest.mark.django_db

# Minimal valid MP3 frame blob (mirrors StimulusFactory's fake clip).
FAKE_MP3 = b"\xff\xfb\x90\x00" + b"\x00" * 512


def _client(user):
    client = Client()
    client.force_login(user)
    return client


def _url(name, exp, **kw):
    return reverse(f"studio:{name}", kwargs={"slug": exp.slug, **kw})


def test_viewer_gets_403_editor_gets_page():
    owner, viewer, editor = UserFactory(), UserFactory(), UserFactory()
    exp = ExperimentFactory(owner=owner)
    Membership.objects.create(experiment=exp, user=viewer, role=Role.VIEWER)
    Membership.objects.create(experiment=exp, user=editor, role=Role.EDITOR)
    assert _client(viewer).get(_url("stimuli", exp)).status_code == 403
    assert _client(editor).get(_url("stimuli", exp)).status_code == 200


def test_create_condition_and_duplicate_name_error():
    owner = UserFactory()
    exp = ExperimentFactory(owner=owner)
    client = _client(owner)

    resp = client.post(_url("condition_add", exp), {"name": "model-A", "description": ""})
    assert resp.status_code == 302
    assert exp.conditions.filter(name="model-A").exists()
    assert AuditEvent.objects.filter(
        experiment=exp, action=AuditEvent.Action.EDIT, target="condition"
    ).exists()

    resp = client.post(_url("condition_add", exp), {"name": "model-A", "description": ""})
    assert resp.status_code == 200  # re-rendered with errors
    assert exp.conditions.filter(name="model-A").count() == 1


def test_create_text_stimulus():
    owner = UserFactory()
    exp = ExperimentFactory(owner=owner)
    cond = ConditionFactory(experiment=exp)
    resp = _client(owner).post(
        _url("stimulus_add", exp),
        {
            "condition": cond.pk,
            "title": "sample-1",
            "description": "",
            "kind": "text",
            "text_body": "The quick brown fox.",
            "embed_url": "",
            "prompt_group": "",
            "is_active": "on",
            "sort_order": 0,
        },
    )
    assert resp.status_code == 302
    stim = Stimulus.objects.get(condition=cond)
    assert stim.kind == Stimulus.Kind.TEXT
    assert AuditEvent.objects.filter(
        experiment=exp, action=AuditEvent.Action.EDIT, target="stimulus"
    ).exists()


def test_create_audio_stimulus_with_upload():
    owner = UserFactory()
    exp = ExperimentFactory(owner=owner)
    cond = ConditionFactory(experiment=exp)
    resp = _client(owner).post(
        _url("stimulus_add", exp),
        {
            "condition": cond.pk,
            "title": "clip-1",
            "description": "",
            "kind": "audio",
            "audio": SimpleUploadedFile("clip.mp3", FAKE_MP3, content_type="audio/mpeg"),
            "text_body": "",
            "embed_url": "",
            "prompt_group": "",
            "is_active": "on",
            "sort_order": 0,
        },
    )
    assert resp.status_code == 302
    stim = Stimulus.objects.get(condition=cond)
    assert stim.kind == Stimulus.Kind.AUDIO
    assert stim.audio
    assert stim.sha256  # checksum computed on save


def test_kind_matrix_violation_shows_error():
    owner = UserFactory()
    exp = ExperimentFactory(owner=owner)
    cond = ConditionFactory(experiment=exp)
    # kind=audio but no audio file → Stimulus.clean() error surfaces in the form
    resp = _client(owner).post(
        _url("stimulus_add", exp),
        {
            "condition": cond.pk,
            "title": "broken",
            "description": "",
            "kind": "audio",
            "text_body": "",
            "embed_url": "",
            "prompt_group": "",
            "sort_order": 0,
        },
    )
    assert resp.status_code == 200
    assert not Stimulus.objects.filter(condition=cond).exists()


def test_bad_extension_upload_rejected():
    owner = UserFactory()
    exp = ExperimentFactory(owner=owner)
    cond = ConditionFactory(experiment=exp)
    resp = _client(owner).post(
        _url("stimulus_add", exp),
        {
            "condition": cond.pk,
            "title": "nope",
            "description": "",
            "kind": "audio",
            "audio": SimpleUploadedFile("evil.exe", b"MZ...", content_type="application/octet-stream"),
            "text_body": "",
            "embed_url": "",
            "prompt_group": "",
            "sort_order": 0,
        },
    )
    assert resp.status_code == 200
    assert not Stimulus.objects.filter(condition=cond).exists()


def test_condition_from_another_study_is_rejected():
    owner = UserFactory()
    exp = ExperimentFactory(owner=owner)
    other = ExperimentFactory(owner=owner)
    foreign_cond = ConditionFactory(experiment=other)
    resp = _client(owner).post(
        _url("stimulus_add", exp),
        {
            "condition": foreign_cond.pk,
            "title": "sneaky",
            "description": "",
            "kind": "text",
            "text_body": "x",
            "embed_url": "",
            "prompt_group": "",
            "sort_order": 0,
        },
    )
    assert resp.status_code == 200  # invalid choice → form error
    assert not Stimulus.objects.filter(condition=foreign_cond, title="sneaky").exists()


def test_mutations_blocked_outside_draft():
    owner = UserFactory()
    exp = ExperimentFactory(owner=owner)
    cond = ConditionFactory(experiment=exp)
    stim = TextStimulusFactory(condition=cond)
    exp.state = Experiment.State.ACTIVE
    exp.save(update_fields=["state"])
    client = _client(owner)

    # List stays viewable, with the read-only banner.
    body = client.get(_url("stimuli", exp)).content.decode()
    assert "read-only" in body

    client.post(_url("condition_add", exp), {"name": "late", "description": ""})
    assert not exp.conditions.filter(name="late").exists()

    client.post(_url("stimulus_delete", exp, pk=stim.pk))
    assert Stimulus.objects.filter(pk=stim.pk).exists()


def test_condition_delete_cascades_stimuli():
    owner = UserFactory()
    exp = ExperimentFactory(owner=owner)
    cond = ConditionFactory(experiment=exp)
    stim = TextStimulusFactory(condition=cond)
    resp = _client(owner).post(_url("condition_delete", exp, pk=cond.pk))
    assert resp.status_code == 302
    assert not Condition.objects.filter(pk=cond.pk).exists()
    assert not Stimulus.objects.filter(pk=stim.pk).exists()


def test_edit_prefills_and_updates():
    owner = UserFactory()
    exp = ExperimentFactory(owner=owner)
    cond = ConditionFactory(experiment=exp)
    stim = TextStimulusFactory(condition=cond, title="before")
    client = _client(owner)
    body = client.get(_url("stimulus_edit", exp, pk=stim.pk)).content.decode()
    assert "before" in body
    client.post(
        _url("stimulus_edit", exp, pk=stim.pk),
        {
            "condition": cond.pk,
            "title": "after",
            "description": "",
            "kind": "text",
            "text_body": stim.text_body,
            "embed_url": "",
            "prompt_group": "",
            "is_active": "on",
            "sort_order": 3,
        },
    )
    stim.refresh_from_db()
    assert stim.title == "after"
    assert stim.sort_order == 3