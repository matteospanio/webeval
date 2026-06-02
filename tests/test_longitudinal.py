"""Longitudinal / multi-phase studies with return visits."""
from __future__ import annotations

import re

import pytest
from django.core.exceptions import ValidationError
from django.test import Client
from django.urls import reverse

from experiments.models import Experiment
from experiments.tests.factories import (
    ConditionFactory,
    ExperimentFactory,
    RatingQuestionFactory,
    TextStimulusFactory,
)
from survey.models import ParticipantSession

pytestmark = pytest.mark.django_db


def _study(slug, **kw):
    kw.setdefault("collect_participant_code", True)
    exp = ExperimentFactory(slug=slug, require_audio_check=False, **kw)
    cond = ConditionFactory(experiment=exp, name="C")
    TextStimulusFactory(condition=cond, title="t", sort_order=0)
    RatingQuestionFactory(experiment=exp, sort_order=0)
    exp.state = Experiment.State.ACTIVE
    exp.save(update_fields=["state"])
    return exp


def _consent(exp):
    return reverse("survey:consent", kwargs={"slug": exp.slug})


def _complete(client, exp, code):
    client.post(_consent(exp), {"agree": "on", "participant_code": code})
    client.post(reverse("survey:instructions", kwargs={"slug": exp.slug}))
    play = reverse("survey:play", kwargs={"slug": exp.slug})
    body = client.get(play).content.decode()
    (qid,) = {int(x) for x in re.findall(r'name="q_(\d+)', body)}
    return client.post(play, {f"q_{qid}": "50"}, follow=True)


# --- flow -------------------------------------------------------------------


def test_followup_requires_completed_predecessor():
    ph1 = _study("lph1")
    ph2 = _study("lph2", follows=ph1)
    resp = Client().post(_consent(ph2), {"agree": "on", "participant_code": "NEW"})
    assert resp.status_code == 200
    assert b"Earlier phase required" in resp.content
    assert not ParticipantSession.objects.filter(experiment=ph2).exists()


def test_followup_proceeds_after_predecessor():
    ph1 = _study("lph3")
    ph2 = _study("lph4", follows=ph1)
    _complete(Client(), ph1, "P-1")

    resp = Client().post(_consent(ph2), {"agree": "on", "participant_code": "P-1"})
    assert resp.status_code in (302, 303)
    assert ParticipantSession.objects.filter(
        experiment=ph2, participant_uid="P-1"
    ).exists()


def test_phase_gap_enforced():
    ph1 = _study("lph5")
    ph2 = _study("lph6", follows=ph1, phase_gap_hours=24)
    _complete(Client(), ph1, "P-1")

    resp = Client().post(_consent(ph2), {"agree": "on", "participant_code": "P-1"})
    assert resp.status_code == 200
    assert b"isn" in resp.content  # "isn't open yet"
    assert not ParticipantSession.objects.filter(experiment=ph2).exists()


def test_followup_already_done_blocked():
    ph1 = _study("lph9")
    ph2 = _study("lph10", follows=ph1)
    _complete(Client(), ph1, "P-2")
    _complete(Client(), ph2, "P-2")  # finishes phase 2 once

    resp = Client().post(_consent(ph2), {"agree": "on", "participant_code": "P-2"})
    assert resp.status_code == 200
    assert b"already completed" in resp.content.lower()
    assert (
        ParticipantSession.objects.filter(
            experiment=ph2, participant_uid="P-2", submitted_at__isnull=False
        ).count()
        == 1
    )


def test_thanks_shows_next_phase_and_code():
    ph1 = _study("lph7")
    _study("lph8", follows=ph1, name="Follow-up Wave")
    resp = _complete(Client(), ph1, "P-9")
    body = resp.content.decode()
    assert "Follow-up Wave" in body
    assert "P-9" in body  # the code to reuse next time


# --- validation -------------------------------------------------------------


def test_followup_needs_participant_code_on_both_phases():
    ph1 = ExperimentFactory(slug="v1", collect_participant_code=True)
    ph2 = ExperimentFactory.build(
        slug="v2", collect_participant_code=False, follows=ph1
    )
    with pytest.raises(ValidationError):
        ph2.full_clean()


def test_phase_chain_rejects_cycle():
    a = ExperimentFactory(slug="cyc-a", collect_participant_code=True)
    b = ExperimentFactory(slug="cyc-b", collect_participant_code=True, follows=a)  # noqa: F841
    a.follows = b
    with pytest.raises(ValidationError):
        a.full_clean()
