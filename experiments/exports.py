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

import os
from typing import Any

from .models import Experiment


SCHEMA_VERSION = 1


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
            filename = os.path.basename(s.audio.name) if s.audio else None
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
