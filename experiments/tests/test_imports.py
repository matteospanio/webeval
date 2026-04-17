"""Round-trip tests for the per-experiment ZIP export/import."""
from __future__ import annotations

import io
import json
import zipfile

import pytest
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import Client
from django.urls import reverse

from experiments.exports import (
    ARCHIVE_SCHEMA_VERSION,
    build_experiment_archive,
    build_reproducibility_bundle,
)
from experiments.imports import import_experiment_archive
from experiments.models import Experiment
from experiments.tests.factories import (
    ChoiceQuestionFactory,
    ConditionFactory,
    ExperimentFactory,
    ImageStimulusFactory,
    RatingQuestionFactory,
    StimulusFactory,
    TextQuestionFactory,
    TextStimulusFactory,
)


pytestmark = pytest.mark.django_db


def _populated_experiment(slug: str = "roundtrip-study") -> Experiment:
    exp = ExperimentFactory(name="Roundtrip study", slug=slug)
    cond_a = ConditionFactory(experiment=exp, name="A", description="baseline")
    cond_b = ConditionFactory(experiment=exp, name="B", description="variant")
    StimulusFactory(condition=cond_a, title="audio-a")
    ImageStimulusFactory(condition=cond_b, title="image-b")
    TextStimulusFactory(condition=cond_a, title="text-a")
    RatingQuestionFactory(experiment=exp, prompt="Quality?")
    ChoiceQuestionFactory(experiment=exp, prompt="Gender?")
    TextQuestionFactory(experiment=exp, prompt="Comments?", page_break_before=True)
    return exp


class TestBuildExperimentArchive:
    def test_archive_has_manifest_and_media(self):
        exp = _populated_experiment()
        archive = build_experiment_archive(exp)
        with zipfile.ZipFile(io.BytesIO(archive)) as zf:
            names = set(zf.namelist())
            assert "manifest.json" in names
            media_files = {n for n in names if n.startswith("media/")}
            # Audio + image, but no text stimulus media.
            assert len(media_files) == 2
            manifest = json.loads(zf.read("manifest.json"))
        assert manifest["schema_version"] == ARCHIVE_SCHEMA_VERSION
        assert manifest["experiment"]["slug"] == exp.slug
        text_entries = [s for s in manifest["stimuli"] if s["kind"] == "text"]
        assert text_entries and text_entries[0]["archive_path"] is None

    def test_archive_preserves_sha256(self):
        exp = _populated_experiment()
        archive = build_experiment_archive(exp)
        with zipfile.ZipFile(io.BytesIO(archive)) as zf:
            manifest = json.loads(zf.read("manifest.json"))
            for entry in manifest["stimuli"]:
                if entry["archive_path"] is None:
                    continue
                import hashlib
                assert (
                    hashlib.sha256(zf.read(entry["archive_path"])).hexdigest()
                    == entry["sha256"]
                )


class TestImportExperimentArchive:
    def test_roundtrip_creates_draft_copy(self):
        original = _populated_experiment("original-study")
        archive = build_experiment_archive(original)

        imported = import_experiment_archive(
            io.BytesIO(archive), slug_override="copied-study"
        )

        assert imported.pk != original.pk
        assert imported.slug == "copied-study"
        assert imported.state == Experiment.State.DRAFT
        assert imported.conditions.count() == 2
        total_stimuli = sum(c.stimuli.count() for c in imported.conditions.all())
        assert total_stimuli == 3
        assert imported.questions.count() == 3

    def test_roundtrip_preserves_media_and_hashes(self):
        original = _populated_experiment("hash-study")
        archive = build_experiment_archive(original)
        imported = import_experiment_archive(
            io.BytesIO(archive), slug_override="hash-copy"
        )

        originals_by_title = {}
        for cond in original.conditions.all():
            for s in cond.stimuli.all():
                originals_by_title[s.title] = s

        for cond in imported.conditions.all():
            for s in cond.stimuli.all():
                src = originals_by_title[s.title]
                assert s.kind == src.kind
                if src.sha256:
                    assert s.sha256 == src.sha256

    def test_slug_collision_rejected(self):
        original = _populated_experiment("taken-slug")
        archive = build_experiment_archive(original)
        with pytest.raises(ValidationError) as excinfo:
            import_experiment_archive(io.BytesIO(archive))
        assert "already exists" in "; ".join(excinfo.value.messages)

    def test_bad_zip_rejected(self):
        with pytest.raises(ValidationError):
            import_experiment_archive(io.BytesIO(b"not a zip file"))

    def test_missing_manifest_rejected(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("something-else.txt", "nope")
        with pytest.raises(ValidationError) as excinfo:
            import_experiment_archive(io.BytesIO(buf.getvalue()))
        assert "manifest.json" in "; ".join(excinfo.value.messages)

    def test_schema_v1_accepted_without_media(self):
        exp = ExperimentFactory(slug="v1-source")
        ConditionFactory(experiment=exp, name="A")
        bundle = build_reproducibility_bundle(exp)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("manifest.json", json.dumps(bundle))

        imported = import_experiment_archive(
            io.BytesIO(buf.getvalue()), slug_override="v1-copy"
        )
        assert imported.state == Experiment.State.DRAFT
        assert imported.conditions.count() == 1


class TestImportAdminView:
    def test_import_url_requires_staff(self):
        client = Client()
        response = client.get(reverse("admin:experiments_experiment_import"))
        assert response.status_code in (302, 403)

    def test_staff_can_render_upload_form(self):
        staff = User.objects.create_user("s", "s@e.org", "pw", is_staff=True, is_superuser=True)
        client = Client()
        client.force_login(staff)
        response = client.get(reverse("admin:experiments_experiment_import"))
        assert response.status_code == 200
        assert b"Import experiment" in response.content

    def test_staff_can_import_via_post(self):
        original = _populated_experiment("admin-view-source")
        archive = build_experiment_archive(original)
        staff = User.objects.create_user("s", "s@e.org", "pw", is_staff=True, is_superuser=True)
        client = Client()
        client.force_login(staff)

        uploaded = io.BytesIO(archive)
        uploaded.name = "bundle.zip"
        response = client.post(
            reverse("admin:experiments_experiment_import"),
            {"archive": uploaded, "slug_override": "admin-view-copy"},
        )
        assert response.status_code == 302
        assert Experiment.objects.filter(slug="admin-view-copy").exists()

    def test_export_zip_endpoint(self):
        exp = _populated_experiment("zip-endpoint")
        staff = User.objects.create_user("s", "s@e.org", "pw", is_staff=True, is_superuser=True)
        client = Client()
        client.force_login(staff)
        url = reverse(
            "admin:experiments_experiment_export_zip", kwargs={"slug": exp.slug}
        )
        response = client.get(url)
        assert response.status_code == 200
        assert response["Content-Type"] == "application/zip"
        with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
            assert "manifest.json" in zf.namelist()
