"""Validation + reproducibility round-trip for the video / html / embed kinds."""
from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile

from experiments.exports import build_experiment_archive
from experiments.imports import import_experiment_archive
from experiments.models import Stimulus
from experiments.tests.factories import (
    ConditionFactory,
    EmbedStimulusFactory,
    ExperimentFactory,
    HtmlStimulusFactory,
    VideoStimulusFactory,
)

pytestmark = pytest.mark.django_db


def test_new_kind_stimuli_validate():
    VideoStimulusFactory().full_clean()
    HtmlStimulusFactory().full_clean()
    EmbedStimulusFactory().full_clean()


def test_video_requires_file():
    cond = ConditionFactory()
    with pytest.raises(ValidationError):
        Stimulus(condition=cond, title="v", kind=Stimulus.Kind.VIDEO).full_clean()


def test_html_requires_text_body():
    cond = ConditionFactory()
    with pytest.raises(ValidationError):
        Stimulus(condition=cond, title="h", kind=Stimulus.Kind.HTML).full_clean()


def test_embed_requires_url():
    cond = ConditionFactory()
    with pytest.raises(ValidationError):
        Stimulus(condition=cond, title="e", kind=Stimulus.Kind.EMBED).full_clean()


def test_video_rejects_foreign_media():
    s = VideoStimulusFactory()
    s.audio = SimpleUploadedFile("a.mp3", b"ID3", content_type="audio/mpeg")
    with pytest.raises(ValidationError):
        s.full_clean()


def test_embed_rejects_uploaded_file():
    s = EmbedStimulusFactory()
    s.image = SimpleUploadedFile("x.png", b"\x89PNG\r\n", content_type="image/png")
    with pytest.raises(ValidationError):
        s.full_clean()


def test_archive_roundtrips_new_kinds():
    exp = ExperimentFactory(slug="repro-mm")
    cond = ConditionFactory(experiment=exp, name="C")
    VideoStimulusFactory(condition=cond, title="vid")
    HtmlStimulusFactory(condition=cond, title="htm")
    EmbedStimulusFactory(condition=cond, title="emb")

    payload = build_experiment_archive(exp)
    new_exp = import_experiment_archive(payload, slug_override="repro-mm-copy")

    by_title = {
        s.title: s for s in Stimulus.objects.filter(condition__experiment=new_exp)
    }
    assert by_title["vid"].kind == Stimulus.Kind.VIDEO
    assert bool(by_title["vid"].video)
    assert by_title["htm"].kind == Stimulus.Kind.HTML
    assert "Hello" in by_title["htm"].text_body
    assert by_title["emb"].kind == Stimulus.Kind.EMBED
    assert by_title["emb"].embed_url == "https://example.org/embed/abc"
