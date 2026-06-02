"""Per-study branding / theming (Epic 5)."""
from __future__ import annotations

import io

import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse
from PIL import Image

from experiments.models import Experiment
from experiments.tests.factories import (
    ConditionFactory,
    ExperimentFactory,
    RatingQuestionFactory,
    TextStimulusFactory,
)

pytestmark = pytest.mark.django_db


def _active_study(**kw):
    exp = ExperimentFactory(require_audio_check=False, **kw)
    cond = ConditionFactory(experiment=exp, name="C")
    TextStimulusFactory(condition=cond, title="t", sort_order=0)
    RatingQuestionFactory(experiment=exp, sort_order=0)
    exp.state = Experiment.State.ACTIVE
    exp.save(update_fields=["state"])
    return exp


def _png_bytes():
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), "blue").save(buf, "PNG")
    return buf.getvalue()


def test_invalid_hex_color_rejected():
    exp = ExperimentFactory.build(brand_primary_color="red")
    with pytest.raises(ValidationError):
        exp.full_clean()


def test_valid_hex_color_ok():
    exp = ExperimentFactory(brand_primary_color="#abcdef")
    exp.full_clean()  # must not raise


def test_color_and_custom_css_render_on_participant_page():
    exp = _active_study(
        brand_primary_color="#123456", brand_custom_css=".survey-main{color:red}"
    )
    body = Client().get(
        reverse("survey:consent", kwargs={"slug": exp.slug})
    ).content.decode()
    assert "#123456" in body
    assert ".survey-main{color:red}" in body


def test_logo_rendered_in_header():
    exp = _active_study()
    exp.brand_logo = SimpleUploadedFile("logo.png", _png_bytes(), "image/png")
    exp.save(update_fields=["brand_logo"])
    body = Client().get(
        reverse("survey:consent", kwargs={"slug": exp.slug})
    ).content.decode()
    assert "<img" in body
    assert "branding/" in body
