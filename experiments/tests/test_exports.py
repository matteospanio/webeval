"""Tests for the experiments.exports reproducibility bundle + printable HTML."""
from __future__ import annotations

import json

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from experiments.exports import build_reproducibility_bundle
from experiments.tests.factories import (
    ChoiceQuestionFactory,
    ConditionFactory,
    ExperimentFactory,
    RatingQuestionFactory,
    StimulusFactory,
    TextQuestionFactory,
)

pytestmark = pytest.mark.django_db


class TestReproducibilityBundle:
    def _populated_experiment(self):
        exp = ExperimentFactory(
            name="Listening study",
            slug="listening-study",
            consent_text="I agree.",
            privacy_contact="pi@example.org",
            privacy_policy_url="https://example.org/privacy",
        )
        cond_a = ConditionFactory(experiment=exp, name="A", description="baseline")
        cond_b = ConditionFactory(experiment=exp, name="B", description="variant")
        StimulusFactory(condition=cond_a, title="clip-a1")
        StimulusFactory(condition=cond_b, title="clip-b1")
        RatingQuestionFactory(experiment=exp, prompt="Quality?")
        ChoiceQuestionFactory(experiment=exp, prompt="Gender?")
        TextQuestionFactory(experiment=exp, prompt="Comments?")
        return exp

    def test_bundle_is_json_serializable(self):
        exp = self._populated_experiment()
        bundle = build_reproducibility_bundle(exp)
        serialized = json.dumps(bundle)
        assert json.loads(serialized) == bundle

    def test_bundle_top_level_shape(self):
        exp = self._populated_experiment()
        bundle = build_reproducibility_bundle(exp)
        assert bundle["schema_version"] == 1
        assert bundle["experiment"]["slug"] == "listening-study"
        assert bundle["experiment"]["state"] == "draft"
        assert bundle["experiment"]["consent_text"] == "I agree."
        assert bundle["experiment"]["assignment_strategy"] == "balanced_random"
        assert len(bundle["conditions"]) == 2
        assert len(bundle["stimuli"]) == 2
        assert len(bundle["questions"]) == 3

    def test_bundle_stimuli_include_checksum_but_no_blob(self):
        exp = self._populated_experiment()
        bundle = build_reproducibility_bundle(exp)
        for entry in bundle["stimuli"]:
            assert "sha256" in entry
            assert "filename" in entry
            assert "audio_base64" not in entry
            assert "audio_data" not in entry

    def test_bundle_questions_carry_config(self):
        exp = self._populated_experiment()
        bundle = build_reproducibility_bundle(exp)
        types = {q["type"] for q in bundle["questions"]}
        assert types == {"rating", "choice", "text"}
        rating = next(q for q in bundle["questions"] if q["type"] == "rating")
        assert rating["config"] == {"min": 0, "max": 100, "step": 1}


class TestPrintableView:
    def test_anonymous_is_forbidden(self):
        exp = ExperimentFactory()
        client = Client()
        url = reverse("experiments:printable", kwargs={"slug": exp.slug})
        response = client.get(url)
        # Staff-only view: either 302 to admin login or 403.
        assert response.status_code in (302, 403)

    def test_staff_can_view_printable_page(self):
        exp = ExperimentFactory(name="Printable study", slug="printable-study")
        ConditionFactory(experiment=exp, name="A")
        RatingQuestionFactory(experiment=exp, prompt="Enjoyment")
        staff = User.objects.create_user(
            "admin",
            "admin@example.org",
            "pw",
            is_staff=True,
        )
        client = Client()
        client.force_login(staff)
        url = reverse("experiments:printable", kwargs={"slug": exp.slug})
        response = client.get(url)
        assert response.status_code == 200
        body = response.content.decode()
        assert "Printable study" in body
        assert "Enjoyment" in body

    def test_json_endpoint_returns_bundle(self):
        exp = ExperimentFactory(slug="json-study")
        ConditionFactory(experiment=exp, name="A")
        staff = User.objects.create_user("s", "s@e.org", "pw", is_staff=True)
        client = Client()
        client.force_login(staff)
        url = reverse("experiments:repro_json", kwargs={"slug": exp.slug})
        response = client.get(url)
        assert response.status_code == 200
        assert response["Content-Type"].startswith("application/json")
        data = json.loads(response.content)
        assert data["experiment"]["slug"] == "json-study"
