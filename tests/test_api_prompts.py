"""Tests for the prompt upload REST API."""
from __future__ import annotations

import hashlib

import pytest
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from apikeys.models import APIKey
from experiments.models import Experiment, Prompt
from experiments.tests.factories import ConditionFactory, ExperimentFactory

pytestmark = pytest.mark.django_db


MP3_BLOB = b"ID3\x03\x00\x00\x00\x00\x00\x00fakedata"


def _url(slug: str) -> str:
    return reverse("api_prompt_upload", kwargs={"slug": slug})


def _staff_client(scopes=("prompts:upload",)) -> APIClient:
    user = User.objects.create_user(
        "promptuser", "p@example.org", "pw", is_staff=True
    )
    _, raw = APIKey.generate(user=user, name="test", scopes=list(scopes))
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Token {raw}")
    return client


def _upload(data: bytes = MP3_BLOB, name: str = "prompt.mp3") -> SimpleUploadedFile:
    return SimpleUploadedFile(name, data, content_type="audio/mpeg")


def test_requires_authentication():
    exp = ExperimentFactory()
    response = APIClient().post(_url(exp.slug), {}, format="multipart")
    assert response.status_code == 401


def test_rejects_token_without_scope():
    exp = ExperimentFactory()
    user = User.objects.create_user("u", "u@e.org", "pw", is_staff=True)
    _, raw = APIKey.generate(user=user, name="t", scopes=["stimuli:upload"])
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Token {raw}")
    response = client.post(
        _url(exp.slug),
        {"prompt_group": "g", "audio": _upload()},
        format="multipart",
    )
    assert response.status_code == 403


def test_returns_404_for_unknown_experiment():
    client = _staff_client()
    response = client.post(_url("nope"), {}, format="multipart")
    assert response.status_code == 404


def test_conflict_when_experiment_not_draft():
    exp = ExperimentFactory()
    ConditionFactory(experiment=exp, name="A")
    exp.state = Experiment.State.ACTIVE
    exp.save(update_fields=["state"])
    client = _staff_client()
    response = client.post(
        _url(exp.slug),
        {"prompt_group": "g", "audio": _upload()},
        format="multipart",
    )
    assert response.status_code == 409


def test_happy_path_creates_prompt():
    exp = ExperimentFactory()
    client = _staff_client()
    response = client.post(
        _url(exp.slug),
        {
            "prompt_group": "song-01",
            "title": "Song 01 intro",
            "description": "First 4 measures",
            "audio": _upload(),
        },
        format="multipart",
    )
    assert response.status_code == 201, response.content
    body = response.json()
    assert body["prompt_group"] == "song-01"
    assert body["sha256"] == hashlib.sha256(MP3_BLOB).hexdigest()

    prompt = Prompt.objects.get(pk=body["id"])
    assert prompt.experiment_id == exp.pk
    assert prompt.title == "Song 01 intro"
    assert prompt.description == "First 4 measures"


def test_duplicate_sha256_is_skipped():
    exp = ExperimentFactory()
    client = _staff_client()
    first = client.post(
        _url(exp.slug),
        {"prompt_group": "g1", "audio": _upload()},
        format="multipart",
    )
    assert first.status_code == 201

    second = client.post(
        _url(exp.slug),
        {"prompt_group": "g2", "audio": _upload()},  # same bytes → same sha256
        format="multipart",
    )
    assert second.status_code == 200
    body = second.json()
    assert body["skipped"] is True
    assert body["reason"] == "duplicate_sha256"
    assert body["prompt_id"] == first.json()["id"]
    assert Prompt.objects.filter(experiment=exp).count() == 1


def test_rejects_duplicate_prompt_group():
    """Different bytes but same prompt_group under the same experiment."""
    exp = ExperimentFactory()
    client = _staff_client()
    first = client.post(
        _url(exp.slug),
        {"prompt_group": "shared", "audio": _upload(data=b"aaa" + MP3_BLOB)},
        format="multipart",
    )
    assert first.status_code == 201
    second = client.post(
        _url(exp.slug),
        {"prompt_group": "shared", "audio": _upload(data=b"bbb" + MP3_BLOB)},
        format="multipart",
    )
    assert second.status_code == 400


def test_rejects_disallowed_extension():
    exp = ExperimentFactory()
    client = _staff_client()
    response = client.post(
        _url(exp.slug),
        {"prompt_group": "g", "audio": _upload(name="prompt.txt")},
        format="multipart",
    )
    assert response.status_code == 400


@override_settings(STIMULUS_MAX_UPLOAD_BYTES=50)
def test_rejects_oversized_file():
    exp = ExperimentFactory()
    client = _staff_client()
    response = client.post(
        _url(exp.slug),
        {"prompt_group": "g", "audio": _upload(data=b"x" * 500)},
        format="multipart",
    )
    assert response.status_code == 400


def test_missing_audio_returns_400():
    exp = ExperimentFactory()
    client = _staff_client()
    response = client.post(
        _url(exp.slug),
        {"prompt_group": "g"},
        format="multipart",
    )
    assert response.status_code == 400
