"""Tests for the stimulus batch-upload and pairwise-answers REST API."""
from __future__ import annotations

import hashlib
import json

import pytest
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from experiments.models import Condition, Experiment, Stimulus
from experiments.tests.factories import (
    ChoiceQuestionFactory,
    ConditionFactory,
    ExperimentFactory,
    PairwiseExperimentFactory,
    StimulusFactory,
)
from survey.models import PairAssignment, ParticipantSession, Response as SurveyResponse

pytestmark = pytest.mark.django_db


MP3_BLOB = b"ID3\x03\x00\x00\x00\x00\x00\x00fakedata"


def _url(slug: str) -> str:
    return reverse("api_stimulus_upload", kwargs={"slug": slug})


def _staff_client() -> tuple[APIClient, User]:
    user = User.objects.create_user(
        "apiuser", "api@example.org", "pw", is_staff=True
    )
    token = Token.objects.create(user=user)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")
    return client, user


def _upload(data: bytes, name: str = "clip.mp3") -> SimpleUploadedFile:
    return SimpleUploadedFile(name, data, content_type="audio/mpeg")


def test_requires_authentication():
    exp = ExperimentFactory()
    response = APIClient().post(_url(exp.slug), {}, format="multipart")
    assert response.status_code == 401


def test_rejects_non_staff_tokens():
    user = User.objects.create_user("joe", "j@e.org", "pw", is_staff=False)
    token = Token.objects.create(user=user)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")
    exp = ExperimentFactory()
    response = client.post(_url(exp.slug), {}, format="multipart")
    assert response.status_code == 403


def test_returns_404_for_unknown_experiment():
    client, _ = _staff_client()
    response = client.post(_url("does-not-exist"), {}, format="multipart")
    assert response.status_code == 404


def test_conflict_when_experiment_not_draft():
    exp = ExperimentFactory(state=Experiment.State.DRAFT)
    ConditionFactory(experiment=exp, name="A")
    StimulusFactory(condition=exp.conditions.get(name="A"))
    exp.state = Experiment.State.ACTIVE
    exp.save(update_fields=["state"])

    client, _ = _staff_client()
    response = client.post(
        _url(exp.slug),
        {
            "condition": "A",
            "title": "new",
            "kind": Stimulus.Kind.AUDIO,
            "audio": _upload(MP3_BLOB),
        },
        format="multipart",
    )
    assert response.status_code == 409
    assert "draft" in response.json()["detail"].lower()


def test_happy_path_creates_condition_and_stimulus():
    exp = ExperimentFactory()
    client, _ = _staff_client()

    response = client.post(
        _url(exp.slug),
        {
            "condition": "novel_condition",
            "prompt_group": "prompt_01",
            "title": "sample-1",
            "description": "Generated from prompt_01",
            "kind": Stimulus.Kind.AUDIO,
            "audio": _upload(MP3_BLOB),
        },
        format="multipart",
    )
    assert response.status_code == 201, response.content
    body = response.json()
    assert body["created_condition"] is True
    assert body["condition"] == "novel_condition"
    assert body["sha256"] == hashlib.sha256(MP3_BLOB).hexdigest()
    assert "id" in body

    stim = Stimulus.objects.get(pk=body["id"])
    assert stim.condition.name == "novel_condition"
    assert stim.condition.experiment_id == exp.pk
    assert stim.prompt_group == "prompt_01"
    assert stim.description == "Generated from prompt_01"
    assert stim.sha256 == hashlib.sha256(MP3_BLOB).hexdigest()


def test_reuses_existing_condition():
    exp = ExperimentFactory()
    ConditionFactory(experiment=exp, name="reuse_me")
    client, _ = _staff_client()

    response = client.post(
        _url(exp.slug),
        {
            "condition": "reuse_me",
            "title": "sample",
            "kind": Stimulus.Kind.AUDIO,
            "audio": _upload(MP3_BLOB),
        },
        format="multipart",
    )
    assert response.status_code == 201, response.content
    assert response.json()["created_condition"] is False
    assert Condition.objects.filter(experiment=exp, name="reuse_me").count() == 1


def test_duplicate_sha256_is_skipped():
    exp = ExperimentFactory()
    client, _ = _staff_client()

    first = client.post(
        _url(exp.slug),
        {
            "condition": "A",
            "title": "first",
            "kind": Stimulus.Kind.AUDIO,
            "audio": _upload(MP3_BLOB),
        },
        format="multipart",
    )
    assert first.status_code == 201
    expected_sha = hashlib.sha256(MP3_BLOB).hexdigest()
    assert first.json()["sha256"] == expected_sha

    second = client.post(
        _url(exp.slug),
        {
            "condition": "A",
            "title": "second-try",
            "kind": Stimulus.Kind.AUDIO,
            "audio": _upload(MP3_BLOB),
        },
        format="multipart",
    )
    assert second.status_code == 200, second.content
    body = second.json()
    assert body == {
        "skipped": True,
        "reason": "duplicate_sha256",
        "sha256": expected_sha,
        "stimulus_id": first.json()["id"],
    }
    assert Stimulus.objects.filter(condition__experiment=exp).count() == 1


def test_rejects_disallowed_extension():
    exp = ExperimentFactory()
    client, _ = _staff_client()
    response = client.post(
        _url(exp.slug),
        {
            "condition": "A",
            "title": "bad-ext",
            "kind": Stimulus.Kind.AUDIO,
            "audio": _upload(MP3_BLOB, name="clip.txt"),
        },
        format="multipart",
    )
    assert response.status_code == 400


@override_settings(STIMULUS_MAX_UPLOAD_BYTES=50)
def test_rejects_oversized_file():
    exp = ExperimentFactory()
    client, _ = _staff_client()
    response = client.post(
        _url(exp.slug),
        {
            "condition": "A",
            "title": "too-big",
            "kind": Stimulus.Kind.AUDIO,
            "audio": _upload(b"x" * 500),
        },
        format="multipart",
    )
    assert response.status_code == 400


def test_missing_audio_field_returns_400():
    exp = ExperimentFactory()
    client, _ = _staff_client()
    response = client.post(
        _url(exp.slug),
        {
            "condition": "A",
            "title": "no-audio",
            "kind": Stimulus.Kind.AUDIO,
        },
        format="multipart",
    )
    assert response.status_code == 400


# --- pairwise-answers endpoint --------------------------------------------


def _pairwise_url(slug: str) -> str:
    return reverse("api_pairwise_answers", kwargs={"slug": slug})


def _build_pairwise_fixture():
    exp = PairwiseExperimentFactory(slug="pw-api")
    cond_a = ConditionFactory(experiment=exp, name="Model-A")
    cond_b = ConditionFactory(experiment=exp, name="Model-B")
    stim_a = StimulusFactory(condition=cond_a, prompt_group="prompt-0")
    stim_b = StimulusFactory(condition=cond_b, prompt_group="prompt-0")
    question = ChoiceQuestionFactory(
        experiment=exp,
        section="stimulus",
        prompt="Which do you prefer?",
        config={"choices": ["A", "B"], "multi": False},
    )
    exp.state = Experiment.State.ACTIVE
    exp.save(update_fields=["state"])

    submitted = ParticipantSession.objects.create(
        experiment=exp,
        last_step=ParticipantSession.Step.DONE,
        consented_at=timezone.now(),
        submitted_at=timezone.now(),
    )
    pair = PairAssignment.objects.create(
        session=submitted,
        stimulus_a=stim_a,
        stimulus_b=stim_b,
        prompt_group="prompt-0",
        position_a=PairAssignment.Position.LEFT,
        sort_order=0,
        listen_duration_a_ms=12_000,
        listen_duration_b_ms=15_000,
    )
    SurveyResponse.objects.create(
        session=submitted,
        pair_assignment=pair,
        question=question,
        answer_value=json.dumps("A"),
    )

    # An abandoned session whose answers must be excluded.
    abandoned = ParticipantSession.objects.create(
        experiment=exp,
        last_step=ParticipantSession.Step.STIMULI,
        consented_at=timezone.now(),
    )
    ab_pair = PairAssignment.objects.create(
        session=abandoned,
        stimulus_a=stim_a,
        stimulus_b=stim_b,
        prompt_group="prompt-0",
        position_a=PairAssignment.Position.RIGHT,
        sort_order=0,
    )
    SurveyResponse.objects.create(
        session=abandoned,
        pair_assignment=ab_pair,
        question=question,
        answer_value=json.dumps("B"),
    )
    return exp


def test_pairwise_answers_requires_auth():
    exp = _build_pairwise_fixture()
    response = APIClient().get(_pairwise_url(exp.slug))
    assert response.status_code == 401


def test_pairwise_answers_returns_submitted_rows_only():
    exp = _build_pairwise_fixture()
    client, _ = _staff_client()
    response = client.get(_pairwise_url(exp.slug))
    assert response.status_code == 200, response.content

    rows = response.json()
    assert isinstance(rows, list)
    assert len(rows) == 1  # the abandoned session must be dropped
    row = rows[0]
    assert row["model_a"] == "Model-A"
    assert row["model_b"] == "Model-B"
    assert row["prompt_group"] == "prompt-0"
    assert row["position_a"] == "left"
    assert row["preferred"] == json.dumps("A")
    assert row["listen_duration_a_ms"] == 12_000
    assert row["listen_duration_b_ms"] == 15_000
    assert row["experiment"] == "pw-api"


def test_pairwise_answers_404_for_unknown_slug():
    client, _ = _staff_client()
    response = client.get(_pairwise_url("does-not-exist"))
    assert response.status_code == 404
