"""Private study access: shared code and single-use invite links."""
from __future__ import annotations

import pytest
from django.test import Client
from django.urls import reverse

from experiments.models import Experiment, ParticipantInvite
from experiments.tests.factories import (
    ConditionFactory,
    ExperimentFactory,
    RatingQuestionFactory,
    TextStimulusFactory,
)
from survey.models import ParticipantSession

pytestmark = pytest.mark.django_db


def _study(slug, **kw):
    exp = ExperimentFactory(slug=slug, require_audio_check=False, **kw)
    cond = ConditionFactory(experiment=exp, name="C")
    TextStimulusFactory(condition=cond, title="t", sort_order=0)
    RatingQuestionFactory(experiment=exp, sort_order=0)
    exp.state = Experiment.State.ACTIVE
    exp.save(update_fields=["state"])
    return exp


def test_public_mode_unchanged():
    exp = _study("acc5")  # default = public
    assert Client().get(reverse("survey:consent", kwargs={"slug": exp.slug})).status_code == 200


def test_code_mode_gate_then_grant():
    exp = _study("acc1", access_mode=Experiment.AccessMode.CODE, access_code="SECRET")
    client = Client()
    consent = reverse("survey:consent", kwargs={"slug": exp.slug})
    access = reverse("survey:access", kwargs={"slug": exp.slug})

    resp = client.get(consent)
    assert resp.status_code == 302 and resp.url == access

    bad = client.post(access, {"access_code": "nope"})
    assert bad.status_code == 200 and b"wasn" in bad.content

    ok = client.post(access, {"access_code": "SECRET"})
    assert ok.status_code == 302 and ok.url == consent
    assert client.get(consent).status_code == 200


def test_code_via_query_param_grants():
    exp = _study("acc2", access_mode=Experiment.AccessMode.CODE, access_code="XYZ")
    consent = reverse("survey:consent", kwargs={"slug": exp.slug})
    assert Client().get(consent + "?code=XYZ").status_code == 200


def test_invite_mode_requires_valid_token():
    exp = _study("acc3", access_mode=Experiment.AccessMode.INVITE)
    invite = ParticipantInvite.objects.create(experiment=exp)
    consent = reverse("survey:consent", kwargs={"slug": exp.slug})
    assert Client().get(consent).status_code == 403
    assert Client().get(consent + f"?invite={invite.token}").status_code == 200


def test_invite_token_is_single_use():
    exp = _study("acc4", access_mode=Experiment.AccessMode.INVITE)
    invite = ParticipantInvite.objects.create(experiment=exp)
    consent = reverse("survey:consent", kwargs={"slug": exp.slug})

    c1 = Client()
    c1.get(consent + f"?invite={invite.token}")
    c1.post(consent, {"agree": "on"})
    invite.refresh_from_db()
    assert invite.used_at is not None
    assert ParticipantSession.objects.count() == 1

    # The same token can't be used again.
    assert Client().get(consent + f"?invite={invite.token}").status_code == 403
