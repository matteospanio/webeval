"""One-click experiment cloning (Epic 5)."""
from __future__ import annotations

import pytest
from django.test import Client
from django.urls import reverse

from accounts.models import Membership
from accounts.roles import Role
from accounts.tests.factories import UserFactory
from experiments.cloning import clone_experiment
from experiments.models import Experiment, Question, Stimulus
from experiments.tests.factories import (
    ChoiceQuestionFactory,
    ConditionFactory,
    ExperimentFactory,
    ImageStimulusFactory,
    RatingQuestionFactory,
    ScreeningQuestionFactory,
    StimulusFactory,
    TextQuestionFactory,
    TextStimulusFactory,
)

pytestmark = pytest.mark.django_db


def _rich_experiment(owner=None):
    exp = ExperimentFactory(slug="src", owner=owner, consent_page_views=99)
    c1 = ConditionFactory(experiment=exp, name="A")
    c2 = ConditionFactory(experiment=exp, name="B")
    StimulusFactory(condition=c1, title="audio-a")
    ImageStimulusFactory(condition=c2, title="image-b")
    TextStimulusFactory(condition=c1, title="text-a")
    RatingQuestionFactory(experiment=exp, sort_order=0)
    ctrl = ChoiceQuestionFactory(experiment=exp, prompt="Gender", sort_order=1)
    dep = TextQuestionFactory(experiment=exp, prompt="Why?", sort_order=2)
    dep.visible_if = {"question": ctrl.pk, "op": "eq", "value": "female"}
    dep.save(update_fields=["visible_if"])
    screen = ScreeningQuestionFactory(experiment=exp, sort_order=0)
    exp.eligibility_rule = {"question": screen.pk, "op": "eq", "value": "Yes"}
    exp.save(update_fields=["eligibility_rule"])
    return exp, ctrl, dep, screen


def test_clone_copies_structure_into_new_draft():
    owner = UserFactory()
    src, *_ = _rich_experiment()
    clone = clone_experiment(src, owner=owner)

    assert clone.pk != src.pk
    assert clone.slug != src.slug
    assert clone.state == Experiment.State.DRAFT
    assert clone.owner == owner
    assert clone.consent_page_views == 0
    assert clone.follows is None
    assert clone.name == f"Copy of {src.name}"
    assert clone.conditions.count() == 2
    assert Stimulus.objects.filter(condition__experiment=clone).count() == 3
    assert clone.questions.count() == src.questions.count()


def test_clone_remaps_skip_logic_and_eligibility():
    src, ctrl, dep, screen = _rich_experiment()
    clone = clone_experiment(src, owner=UserFactory())

    cloned_dep = clone.questions.get(prompt="Why?")
    cloned_ctrl = clone.questions.get(prompt="Gender")
    assert cloned_dep.visible_if["question"] == cloned_ctrl.pk
    assert cloned_dep.visible_if["question"] != ctrl.pk

    cloned_screen = clone.questions.get(section=Question.Section.SCREENING)
    assert clone.eligibility_rule["question"] == cloned_screen.pk
    assert clone.eligibility_rule["question"] != screen.pk


def test_clone_copies_media_bytes_independently():
    src, *_ = _rich_experiment()
    src_audio = Stimulus.objects.get(condition__experiment=src, title="audio-a")
    clone = clone_experiment(src, owner=UserFactory())
    clone_audio = Stimulus.objects.get(condition__experiment=clone, title="audio-a")
    assert clone_audio.audio.name != src_audio.audio.name  # its own stored file
    assert clone_audio.sha256 == src_audio.sha256  # identical bytes


def test_studio_clone_view_creates_owned_copy():
    owner = UserFactory()
    src, *_ = _rich_experiment(owner=owner)
    client = Client()
    client.force_login(owner)
    resp = client.post(reverse("studio:study_clone", kwargs={"slug": src.slug}))
    assert resp.status_code == 302
    clone = Experiment.objects.exclude(pk=src.pk).get(owner=owner)
    assert clone.state == Experiment.State.DRAFT
    assert Membership.objects.filter(
        experiment=clone, user=owner, role=Role.OWNER
    ).exists()
    assert resp["Location"] == reverse(
        "studio:study_overview", kwargs={"slug": clone.slug}
    )
