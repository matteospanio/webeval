"""Import a per-experiment archive produced by :func:`experiments.exports.build_experiment_archive`.

The archive is a single ZIP containing a ``manifest.json`` (see
:func:`experiments.exports.build_reproducibility_bundle` plus an
``archive_path`` per stimulus) and a ``media/`` folder with the raw audio /
image files. Importing always creates a brand-new Experiment in DRAFT state
— the only state that allows child writes under the ``_ensure_draft`` guard
in :mod:`experiments.models`.
"""
from __future__ import annotations

import json
import os
import zipfile
from typing import IO, Any

from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.db import transaction

from .exports import ARCHIVE_SCHEMA_VERSION, SCHEMA_VERSION
from .models import Condition, Experiment, Question, Stimulus


_EXPERIMENT_SCALAR_FIELDS = (
    "name",
    "description",
    "consent_text",
    "instructions_content",
    "thanks_content",
    "privacy_contact",
    "privacy_policy_url",
    "mode",
    "stimuli_per_participant",
    "assignment_strategy",
    "require_audio_check",
)


def import_experiment_archive(
    file_or_bytes: IO[bytes] | bytes,
    *,
    slug_override: str | None = None,
) -> Experiment:
    """Create a DRAFT Experiment from a ZIP archive.

    Raises :class:`django.core.exceptions.ValidationError` when the archive
    is malformed, the schema version is unsupported, or the target slug is
    already taken.
    """
    try:
        zf = zipfile.ZipFile(file_or_bytes)
    except zipfile.BadZipFile as exc:
        raise ValidationError(f"Uploaded file is not a valid ZIP archive: {exc}")

    with zf:
        try:
            manifest_bytes = zf.read("manifest.json")
        except KeyError:
            raise ValidationError("Archive is missing manifest.json at the root.")
        try:
            manifest = json.loads(manifest_bytes)
        except json.JSONDecodeError as exc:
            raise ValidationError(f"manifest.json is not valid JSON: {exc}")

        schema = manifest.get("schema_version")
        if schema not in (SCHEMA_VERSION, ARCHIVE_SCHEMA_VERSION):
            raise ValidationError(
                f"Unsupported schema_version {schema!r}; "
                f"expected {SCHEMA_VERSION} or {ARCHIVE_SCHEMA_VERSION}."
            )
        for key in ("experiment", "conditions", "stimuli", "questions"):
            if key not in manifest:
                raise ValidationError(f"manifest.json missing required key: {key!r}")

        exp_data = manifest["experiment"]
        slug = slug_override or exp_data.get("slug") or ""
        slug = slug.strip()
        if not slug:
            raise ValidationError("No slug in manifest and no override supplied.")
        if Experiment.objects.filter(slug=slug).exists():
            raise ValidationError(
                f"An experiment with slug '{slug}' already exists. "
                "Provide a different slug via the override field to import a copy."
            )

        with transaction.atomic():
            experiment = _create_experiment(exp_data, slug=slug)
            condition_map = _create_conditions(experiment, manifest["conditions"])
            _create_stimuli(condition_map, manifest["stimuli"], zf)
            _create_questions(experiment, manifest["questions"])

    return experiment


def _create_experiment(exp_data: dict[str, Any], *, slug: str) -> Experiment:
    kwargs: dict[str, Any] = {"slug": slug, "state": Experiment.State.DRAFT}
    for field in _EXPERIMENT_SCALAR_FIELDS:
        if field in exp_data:
            kwargs[field] = exp_data[field]
    return Experiment.objects.create(**kwargs)


def _create_conditions(
    experiment: Experiment, conditions: list[dict[str, Any]]
) -> dict[int, Condition]:
    mapping: dict[int, Condition] = {}
    for entry in conditions:
        cond = Condition.objects.create(
            experiment=experiment,
            name=entry["name"],
            description=entry.get("description", ""),
        )
        mapping[entry["id"]] = cond
    return mapping


def _create_stimuli(
    condition_map: dict[int, Condition],
    stimuli: list[dict[str, Any]],
    zf: zipfile.ZipFile,
) -> None:
    archive_names = set(zf.namelist())
    for entry in stimuli:
        condition = condition_map.get(entry["condition_id"])
        if condition is None:
            raise ValidationError(
                f"Stimulus {entry.get('id')!r} references unknown condition "
                f"{entry.get('condition_id')!r}."
            )
        kind = entry.get("kind", Stimulus.Kind.AUDIO)
        stim = Stimulus(
            condition=condition,
            title=entry["title"],
            description=entry.get("description", ""),
            kind=kind,
            prompt_group=entry.get("prompt_group") or "",
            is_active=entry.get("is_active", True),
            sort_order=entry.get("sort_order", 0),
            sha256=entry.get("sha256") or "",
            duration_seconds=entry.get("duration_seconds"),
        )
        archive_path = entry.get("archive_path")
        filename = entry.get("filename")
        if kind == Stimulus.Kind.TEXT:
            stim.text_body = entry.get("text_body") or entry.get("description") or ""
            if not stim.text_body.strip():
                stim.text_body = entry.get("title", "")
        elif archive_path:
            if archive_path not in archive_names:
                raise ValidationError(
                    f"Archive is missing media file '{archive_path}' referenced by "
                    f"stimulus '{entry.get('title')}'."
                )
            data = zf.read(archive_path)
            basename = filename or os.path.basename(archive_path)
            content = ContentFile(data, name=basename)
            if kind == Stimulus.Kind.AUDIO:
                stim.audio = content
            elif kind == Stimulus.Kind.IMAGE:
                stim.image = content
        stim.save()


def _create_questions(experiment: Experiment, questions: list[dict[str, Any]]) -> None:
    for entry in questions:
        Question.objects.create(
            experiment=experiment,
            section=entry["section"],
            type=entry["type"],
            prompt=entry.get("prompt", ""),
            help_text=entry.get("help_text", ""),
            required=entry.get("required", True),
            config=entry.get("config") or {},
            sort_order=entry.get("sort_order", 0),
            page_break_before=entry.get("page_break_before", False),
            show_prompt=entry.get("show_prompt", False),
        )
