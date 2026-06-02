"""Epic 7 — compliance & governance: metadata, consent versioning, audit,
retention, PII redaction, and the data-subject-request workflow."""
from __future__ import annotations

import json
from datetime import timedelta

import pytest
from django.core.management import call_command
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from accounts.models import AuditEvent
from accounts.tests.factories import UserFactory
from experiments.models import Question
from experiments.tests.factories import (
    ConditionFactory,
    ExperimentFactory,
    RatingQuestionFactory,
    StimulusFactory,
    TextQuestionFactory,
)
from survey.models import ParticipantSession, Response

pytestmark = pytest.mark.django_db


def _completed(exp, **kw):
    return ParticipantSession.objects.create(
        experiment=exp, last_step=ParticipantSession.Step.DONE,
        consented_at=timezone.now(), submitted_at=timezone.now(), **kw,
    )


def _owner_client(**kw):
    owner = UserFactory()
    exp = ExperimentFactory(owner=owner, **kw)
    client = Client()
    client.force_login(owner)
    return client, exp, owner


# --- consent versioning -----------------------------------------------------


def test_consent_version_tracks_text():
    e1 = ExperimentFactory(consent_text="Version one")
    e2 = ExperimentFactory(consent_text="Version two")
    assert len(e1.consent_version) == 12
    assert e1.consent_version != e2.consent_version


def test_session_records_consent_version():
    from experiments.models import Experiment

    exp = ExperimentFactory(consent_text="Please agree", require_audio_check=False)
    exp.state = Experiment.State.ACTIVE
    exp.save(update_fields=["state"])
    Client().post(reverse("survey:consent", kwargs={"slug": exp.slug}), {"agree": "on"})
    session = ParticipantSession.objects.get(experiment=exp)
    assert session.consent_version == exp.consent_version


# --- PII redaction ----------------------------------------------------------


def test_pii_answer_redacted_unless_included():
    client, exp, owner = _owner_client()
    cond = ConditionFactory(experiment=exp)
    stim = StimulusFactory(condition=cond)
    q = TextQuestionFactory(
        experiment=exp, section=Question.Section.STIMULUS, contains_pii=True
    )
    s = _completed(exp)
    Response.objects.create(
        session=s, stimulus=stim, question=q, answer_value=json.dumps("a@b.com")
    )
    url = reverse("studio:answers_csv", kwargs={"slug": exp.slug})
    body = client.get(url).content.decode()
    assert "a@b.com" not in body and "[redacted]" in body
    body2 = client.get(url + "?include_pii=1").content.decode()
    assert "a@b.com" in body2


# --- audit trail ------------------------------------------------------------


def test_export_is_audited():
    client, exp, owner = _owner_client()
    client.get(reverse("studio:answers_csv", kwargs={"slug": exp.slug}))
    assert AuditEvent.objects.filter(
        experiment=exp, action=AuditEvent.Action.EXPORT, target="answers.csv"
    ).exists()


# --- retention sweep --------------------------------------------------------


def test_retention_deletes_only_expired():
    exp = ExperimentFactory(retention_days=30)
    old = _completed(exp)
    old.submitted_at = timezone.now() - timedelta(days=40)
    old.save(update_fields=["submitted_at"])
    recent = _completed(exp)

    call_command("purge_expired_data")
    assert not ParticipantSession.objects.filter(pk=old.pk).exists()
    assert ParticipantSession.objects.filter(pk=recent.pk).exists()
    assert AuditEvent.objects.filter(
        experiment=exp, action=AuditEvent.Action.DELETE, target="retention"
    ).exists()


def test_retention_dry_run_and_zero_keep_data():
    exp = ExperimentFactory(retention_days=30)
    old = _completed(exp)
    old.submitted_at = timezone.now() - timedelta(days=40)
    old.save(update_fields=["submitted_at"])
    call_command("purge_expired_data", "--dry-run")
    assert ParticipantSession.objects.filter(pk=old.pk).exists()

    exp.retention_days = 0
    exp.save(update_fields=["retention_days"])
    call_command("purge_expired_data")
    assert ParticipantSession.objects.filter(pk=old.pk).exists()


# --- data-subject request ---------------------------------------------------


def test_dsr_search_export_and_erase():
    client, exp, owner = _owner_client()
    cond = ConditionFactory(experiment=exp)
    stim = StimulusFactory(condition=cond)
    q = RatingQuestionFactory(experiment=exp)
    s = _completed(exp, participant_uid="P-123")
    Response.objects.create(
        session=s, stimulus=stim, question=q, answer_value=json.dumps(50)
    )
    dsr = reverse("studio:dsr")

    # search finds the session
    assert str(s.id) in client.get(dsr + "?identifier=P-123").content.decode()

    # export returns the participant's data as JSON
    resp = client.post(dsr, {"identifier": "P-123", "action": "export"})
    data = json.loads(resp.content)
    assert data["identifier"] == "P-123" and len(data["sessions"]) == 1

    # erase (owner can manage their own study)
    client.post(dsr, {"identifier": "P-123", "action": "delete"})
    s.refresh_from_db()
    assert s.last_step == ParticipantSession.Step.WITHDRAWN
    assert not Response.objects.filter(session=s).exists()
    assert AuditEvent.objects.filter(
        action=AuditEvent.Action.DELETE, target="DSR:P-123"
    ).exists()


# --- compliance metadata surfacing ------------------------------------------


def test_overview_shows_compliance_metadata():
    client, exp, owner = _owner_client(irb_number="IRB-9", retention_days=90)
    body = client.get(
        reverse("studio:study_overview", kwargs={"slug": exp.slug})
    ).content.decode()
    assert "Compliance" in body
    assert "IRB-9" in body
    assert "90 days" in body
    assert "consent version" in body.lower()
