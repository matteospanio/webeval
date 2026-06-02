"""Epic 8 — integrations: webhooks, operator email, public API read endpoints."""
from __future__ import annotations

import hashlib
import hmac
import json
import re

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.tests.factories import UserFactory
from apikeys.models import APIKey
from experiments.models import Experiment, Question, Webhook
from experiments.tests.factories import (
    ConditionFactory,
    ExperimentFactory,
    RatingQuestionFactory,
    StimulusFactory,
    TextQuestionFactory,
    TextStimulusFactory,
)
from survey.models import ParticipantSession, Response

pytestmark = pytest.mark.django_db


def _active_study(slug, **kw):
    exp = ExperimentFactory(slug=slug, require_audio_check=False, **kw)
    cond = ConditionFactory(experiment=exp, name="C")
    TextStimulusFactory(condition=cond, title="t", sort_order=0)
    RatingQuestionFactory(experiment=exp, sort_order=0)
    exp.state = Experiment.State.ACTIVE
    exp.save(update_fields=["state"])
    return exp


def _complete(client, exp):
    client.post(reverse("survey:consent", kwargs={"slug": exp.slug}), {"agree": "on"})
    client.post(reverse("survey:instructions", kwargs={"slug": exp.slug}))
    play = reverse("survey:play", kwargs={"slug": exp.slug})
    body = client.get(play).content.decode()
    (qid,) = {int(x) for x in re.findall(r'name="q_(\d+)', body)}
    client.post(play, {f"q_{qid}": "50"}, follow=True)


def _key_client(scopes):
    user = User.objects.create_user("apiuser", "a@e.org", "pw", is_staff=True)
    _, raw = APIKey.generate(user=user, name="t", scopes=list(scopes))
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Token {raw}")
    return client


# --- webhooks ---------------------------------------------------------------


def test_deliver_event_signs_payload_and_records_status(monkeypatch):
    exp = ExperimentFactory()
    hook = Webhook.objects.create(
        experiment=exp, url="https://hooks.test/x", event="session.completed"
    )
    session = ParticipantSession.objects.create(
        experiment=exp, submitted_at=timezone.now(), completion_code="C1",
        external_id="E1",
    )
    calls = []
    monkeypatch.setattr(
        "experiments.webhooks._post",
        lambda url, body, headers: calls.append((url, body, headers)) or 200,
    )
    from experiments.webhooks import deliver_event

    assert deliver_event(session, "session.completed") == 1
    url, body, headers = calls[0]
    assert url == "https://hooks.test/x"
    payload = json.loads(body)
    assert payload["event"] == "session.completed"
    assert payload["session_id"] == str(session.id)
    expected = "sha256=" + hmac.new(
        hook.secret.encode(), body, hashlib.sha256
    ).hexdigest()
    assert headers["X-Webhook-Signature"] == expected
    hook.refresh_from_db()
    assert hook.last_status == 200 and hook.last_delivered_at is not None


def test_completion_fires_webhook(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "experiments.webhooks._post",
        lambda url, body, headers: calls.append(url) or 200,
    )
    exp = _active_study("wh1")
    Webhook.objects.create(experiment=exp, url="https://hooks.test/done")
    _complete(Client(), exp)
    assert calls == ["https://hooks.test/done"]


def test_completion_emails_operator(mailoutbox):
    exp = _active_study("wh2", notify_email="ops@example.org")
    _complete(Client(), exp)
    assert len(mailoutbox) == 1
    assert "ops@example.org" in mailoutbox[0].to


# --- public API read endpoints ----------------------------------------------


def test_api_answers_endpoint_redacts_pii():
    exp = ExperimentFactory()
    cond = ConditionFactory(experiment=exp)
    stim = StimulusFactory(condition=cond)
    q = TextQuestionFactory(
        experiment=exp, section=Question.Section.STIMULUS, contains_pii=True
    )
    s = ParticipantSession.objects.create(experiment=exp, submitted_at=timezone.now())
    Response.objects.create(
        session=s, stimulus=stim, question=q, answer_value=json.dumps("me@x.com")
    )
    client = _key_client(["answers:read"])
    url = reverse("api_answers", kwargs={"slug": exp.slug})
    data = client.get(url).json()
    assert len(data) == 1 and data[0]["answer"] == "[redacted]"
    assert client.get(url + "?include_pii=1").json()[0]["answer"] == "me@x.com"


def test_api_results_endpoint():
    exp = ExperimentFactory()
    cond = ConditionFactory(experiment=exp)
    stim = StimulusFactory(condition=cond)
    q = RatingQuestionFactory(experiment=exp)
    s = ParticipantSession.objects.create(experiment=exp, submitted_at=timezone.now())
    Response.objects.create(
        session=s, stimulus=stim, question=q, answer_value=json.dumps(80)
    )
    data = _key_client(["results:read"]).get(
        reverse("api_results", kwargs={"slug": exp.slug})
    ).json()
    assert any(r["type"] == "rating" and r["kind"] == "numeric" for r in data)


def test_api_read_requires_scope():
    exp = ExperimentFactory()
    resp = _key_client(["stimuli:upload"]).get(
        reverse("api_results", kwargs={"slug": exp.slug})
    )
    assert resp.status_code == 403


# --- studio webhook management ----------------------------------------------


def test_studio_webhook_add_and_delete():
    owner = UserFactory()
    exp = ExperimentFactory(owner=owner)
    client = Client()
    client.force_login(owner)
    url = reverse("studio:study_webhooks", kwargs={"slug": exp.slug})

    client.post(url, {"action": "add", "url": "https://h/x", "event": "session.completed"})
    hook = Webhook.objects.get(experiment=exp)
    assert hook.url == "https://h/x"

    client.post(url, {"action": "delete", "webhook_id": hook.pk})
    assert not Webhook.objects.filter(pk=hook.pk).exists()
