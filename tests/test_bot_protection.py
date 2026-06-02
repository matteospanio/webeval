"""Bot protection: honeypot field on the consent form."""
from __future__ import annotations

import pytest
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


def _study(slug, bot=False):
    exp = ExperimentFactory(slug=slug, require_audio_check=False, bot_protection=bot)
    cond = ConditionFactory(experiment=exp, name="C")
    TextStimulusFactory(condition=cond, title="t", sort_order=0)
    RatingQuestionFactory(experiment=exp, sort_order=0)
    exp.state = Experiment.State.ACTIVE
    exp.save(update_fields=["state"])
    return exp


def _consent(exp):
    return reverse("survey:consent", kwargs={"slug": exp.slug})


def test_honeypot_blocks_bot():
    exp = _study("bot1", bot=True)
    Client().post(_consent(exp), {"agree": "on", "hp_url": "http://spam.example"})
    assert ParticipantSession.objects.count() == 0


def test_empty_honeypot_proceeds():
    exp = _study("bot2", bot=True)
    Client().post(_consent(exp), {"agree": "on", "hp_url": ""})
    assert ParticipantSession.objects.count() == 1


def test_honeypot_ignored_when_protection_off():
    exp = _study("bot3", bot=False)
    Client().post(_consent(exp), {"agree": "on", "hp_url": "http://spam.example"})
    assert ParticipantSession.objects.count() == 1
