"""Duplicate an experiment (with all its children) into a fresh DRAFT.

Unlike the ZIP archive round-trip (:mod:`experiments.exports` /
:mod:`experiments.imports`), this in-process clone is **faithful**: it carries
over every authored field, including skip logic (``visible_if``), attention
checks, the screening ``eligibility_rule``, access/branding config, and the
raw media bytes of audio/image/video stimuli and pairwise prompts. Question
references inside ``visible_if`` / ``eligibility_rule`` are remapped to the
cloned questions' new ids.

The clone is always created in DRAFT state so the structural-edit lock lets
the new owner keep editing. Granting the owner ``Membership`` is left to the
caller (studio/admin) so this module stays free of an ``accounts`` import and
the one-way dependency ``studio -> accounts -> experiments`` is preserved.
"""
from __future__ import annotations

import os
from typing import Any

from django.core.files.base import ContentFile
from django.db import transaction
from django.utils.text import slugify

from .models import Condition, Experiment, Prompt, Question, Stimulus

# Experiment fields that must not be carried over verbatim on a clone.
_EXPERIMENT_SKIP = {
    "id",
    "name",
    "slug",
    "owner",
    "state",
    "created_at",
    "updated_at",
    "consent_page_views",
    "follows",
}


def _unique_slug(base: str) -> str:
    base = slugify(base)[:185] or "study"
    slug = base
    i = 2
    while Experiment.objects.filter(slug=slug).exists():
        slug = f"{base}-{i}"
        i += 1
    return slug


def _copy_file(src_field, dest_instance, dest_attr: str) -> None:
    """Copy a stored FileField's bytes into a new file on ``dest_instance``."""
    if not src_field:
        return
    src_field.open("rb")
    try:
        data = src_field.read()
    finally:
        src_field.close()
    name = os.path.basename(src_field.name)
    getattr(dest_instance, dest_attr).save(name, ContentFile(data), save=False)


def _remap_rule(rule: Any, q_map: dict[int, int]) -> Any:
    """Rewrite the question ids inside a visible_if / eligibility_rule clause."""
    if not isinstance(rule, dict):
        return rule
    if "question" in rule:
        out = dict(rule)
        out["question"] = q_map.get(rule["question"], rule["question"])
        return out
    for key in ("all", "any"):
        if isinstance(rule.get(key), list):
            out = dict(rule)
            out[key] = [_remap_rule(clause, q_map) for clause in rule[key]]
            return out
    return rule


def _copy_fields(src, dest, skip: set[str]) -> None:
    for field in type(src)._meta.concrete_fields:
        if field.name in skip:
            continue
        setattr(dest, field.attname, getattr(src, field.attname))


@transaction.atomic
def clone_experiment(
    experiment: Experiment, *, owner, name: str | None = None
) -> Experiment:
    """Deep-copy ``experiment`` into a new DRAFT owned by ``owner``."""
    new = Experiment()
    _copy_fields(experiment, new, _EXPERIMENT_SKIP)
    new.name = name or f"Copy of {experiment.name}"
    new.slug = _unique_slug(new.name)
    new.owner = owner
    new.state = Experiment.State.DRAFT
    new.consent_page_views = 0
    new.follows = None
    new.save()

    condition_map: dict[int, Condition] = {}
    for cond in experiment.conditions.all():
        new_cond = Condition(
            experiment=new, name=cond.name, description=cond.description
        )
        new_cond.save()
        condition_map[cond.pk] = new_cond

    stimulus_skip = {"id", "condition", "audio", "image", "video", "sha256"}
    for stim in Stimulus.objects.filter(condition__experiment=experiment):
        new_stim = Stimulus()
        _copy_fields(stim, new_stim, stimulus_skip)
        new_stim.condition = condition_map[stim.condition_id]
        new_stim.sha256 = ""
        if stim.kind == Stimulus.Kind.AUDIO:
            _copy_file(stim.audio, new_stim, "audio")
        elif stim.kind == Stimulus.Kind.IMAGE:
            _copy_file(stim.image, new_stim, "image")
        elif stim.kind == Stimulus.Kind.VIDEO:
            _copy_file(stim.video, new_stim, "video")
        new_stim.save()

    question_map: dict[int, int] = {}
    cloned_questions: list[Question] = []
    for q in experiment.questions.all().order_by("section", "sort_order", "id"):
        new_q = Question()
        _copy_fields(q, new_q, {"id", "experiment"})
        new_q.experiment = new
        new_q.save()
        question_map[q.pk] = new_q.pk
        cloned_questions.append(new_q)

    # Now that every question has a new id, rewrite skip-logic references.
    for new_q in cloned_questions:
        if new_q.visible_if:
            new_q.visible_if = _remap_rule(new_q.visible_if, question_map)
            new_q.save(update_fields=["visible_if"])

    if new.eligibility_rule:
        new.eligibility_rule = _remap_rule(new.eligibility_rule, question_map)
        new.save(update_fields=["eligibility_rule"])

    for prompt in experiment.prompts.all():
        new_prompt = Prompt(
            experiment=new,
            prompt_group=prompt.prompt_group,
            title=prompt.title,
            description=prompt.description,
        )
        _copy_file(prompt.audio, new_prompt, "audio")
        new_prompt.save()

    return new
