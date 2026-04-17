"""Reproducibility exports for an Experiment.

Two sibling artefacts are produced:

* ``build_reproducibility_bundle(experiment)`` returns a JSON-serializable
  ``dict`` with schema version, full experiment metadata, conditions, stimuli
  (filenames + SHA-256 checksums, **no audio blobs**), questions with their
  per-type ``config``, and the consent text. Round-trips through
  :func:`json.dumps` / :func:`json.loads`.
* The companion printable HTML view renders the same data in a template that
  is friendly to "print to PDF" for supplementary material.

Both are gated behind ``@staff_member_required`` since they expose the full
experiment configuration.
"""
from __future__ import annotations

import io
import json
import os
import zipfile
from typing import Any

from .models import Experiment


SCHEMA_VERSION = 1
ARCHIVE_SCHEMA_VERSION = 2


def build_reproducibility_bundle(experiment: Experiment) -> dict[str, Any]:
    """Return a JSON-serialisable dict describing ``experiment`` in full.

    Audio blobs are intentionally excluded; each stimulus carries a filename
    plus a SHA-256 checksum so the companion audio archive can be verified
    against the bundle.
    """
    exp_data = {
        "id": experiment.pk,
        "slug": experiment.slug,
        "name": experiment.name,
        "description": experiment.description,
        "state": experiment.state,
        "mode": experiment.mode,
        "consent_text": experiment.consent_text,
        "instructions_content": experiment.instructions_content,
        "thanks_content": experiment.thanks_content,
        "privacy_contact": experiment.privacy_contact,
        "privacy_policy_url": experiment.privacy_policy_url,
        "stimuli_per_participant": experiment.stimuli_per_participant,
        "assignment_strategy": experiment.assignment_strategy,
        "require_audio_check": experiment.require_audio_check,
        "created_at": experiment.created_at.isoformat() if experiment.created_at else None,
    }

    conditions = [
        {
            "id": cond.pk,
            "name": cond.name,
            "description": cond.description,
        }
        for cond in experiment.conditions.all().order_by("name")
    ]

    stimuli = []
    for stim in (
        Experiment.objects.get(pk=experiment.pk)
        .conditions.all()
        .prefetch_related("stimuli")
    ):
        for s in stim.stimuli.all().order_by("sort_order", "title"):
            if s.kind == s.Kind.AUDIO and s.audio:
                filename = os.path.basename(s.audio.name)
            elif s.kind == s.Kind.IMAGE and s.image:
                filename = os.path.basename(s.image.name)
            else:
                filename = None
            stimuli.append(
                {
                    "id": s.pk,
                    "condition_id": stim.pk,
                    "condition_name": stim.name,
                    "title": s.title,
                    "description": s.description,
                    "kind": s.kind,
                    "prompt_group": s.prompt_group or None,
                    "filename": filename,
                    "sha256": s.sha256 or None,
                    "duration_seconds": s.duration_seconds,
                    "is_active": s.is_active,
                    "sort_order": s.sort_order,
                    "text_body": s.text_body if s.kind == s.Kind.TEXT else "",
                }
            )

    questions = [
        {
            "id": q.pk,
            "section": q.section,
            "type": q.type,
            "prompt": q.prompt,
            "help_text": q.help_text,
            "required": q.required,
            "config": q.config,
            "sort_order": q.sort_order,
            "page_break_before": q.page_break_before,
            "show_prompt": q.show_prompt,
        }
        for q in experiment.questions.all().order_by("section", "sort_order", "pk")
    ]

    return {
        "schema_version": SCHEMA_VERSION,
        "experiment": exp_data,
        "conditions": conditions,
        "stimuli": stimuli,
        "questions": questions,
    }


def _stimulus_media_source(stim):
    """Return (field_file, filename) for the media attached to a stimulus, or (None, None)."""
    if stim.kind == stim.Kind.AUDIO and stim.audio:
        return stim.audio, os.path.basename(stim.audio.name)
    if stim.kind == stim.Kind.IMAGE and stim.image:
        return stim.image, os.path.basename(stim.image.name)
    return None, None


def build_experiment_archive(experiment: Experiment) -> bytes:
    """Return the raw bytes of a single-file ZIP bundling ``experiment``.

    The archive contains:

    * ``manifest.json`` — the reproducibility bundle from
      :func:`build_reproducibility_bundle` upgraded to
      ``schema_version = 2`` with each stimulus entry carrying an
      ``archive_path`` pointing at its media file (``None`` for text).
    * ``media/<stimulus_pk>.<ext>`` — the raw bytes of each audio / image
      stimulus, named by pk so renames at import time cannot break the link.

    The companion import path at :func:`experiments.imports.import_experiment_archive`
    consumes this format.
    """
    bundle = build_reproducibility_bundle(experiment)
    bundle["schema_version"] = ARCHIVE_SCHEMA_VERSION

    stimuli_by_id = {s.pk: s for s in _iter_stimuli(experiment)}

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for entry in bundle["stimuli"]:
            stim = stimuli_by_id.get(entry["id"])
            archive_path = None
            if stim is not None:
                field_file, filename = _stimulus_media_source(stim)
                if field_file is not None and filename:
                    _, ext = os.path.splitext(filename)
                    archive_path = f"media/{stim.pk}{ext}"
                    try:
                        field_file.open("rb")
                        field_file.seek(0)
                        data = field_file.read()
                    finally:
                        try:
                            field_file.seek(0)
                        except Exception:
                            pass
                    zf.writestr(archive_path, data)
                    entry["filename"] = filename
            entry["archive_path"] = archive_path
        zf.writestr("manifest.json", json.dumps(bundle, indent=2))
    return buf.getvalue()


def _iter_stimuli(experiment: Experiment):
    for cond in experiment.conditions.all().prefetch_related("stimuli"):
        for s in cond.stimuli.all():
            yield s
