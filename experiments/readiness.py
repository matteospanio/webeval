"""Activation-readiness checks for experiments.

Going *live* (or into the TEST/preview phase) on an empty or inconsistent
study wastes participants and produces unusable data. These helpers answer
two questions:

* :func:`is_walkable` — does the experiment have the bare minimum structure
  for a participant to walk the flow at all? (gate for entering TEST/preview)
* :func:`readiness_problems` — a list of human-readable reasons the study is
  not ready to collect real data. An empty list means "ready to activate".

The functions are pure (return data, raise nothing) so the model layer, the
admin activate view, and the studio overview can all consume them. They are
imported lazily from :meth:`experiments.models.Experiment.clean` to keep
``experiments.models`` free of import cycles.
"""
from __future__ import annotations

from experiments.models import Experiment, Question, Stimulus


def _active_stimuli(experiment: Experiment) -> list[Stimulus]:
    return list(
        Stimulus.objects.filter(condition__experiment=experiment, is_active=True)
    )


def is_walkable(experiment: Experiment) -> bool:
    """Minimum structure to run the participant flow (gate for TEST/preview)."""
    return (
        experiment.conditions.exists()
        and Stimulus.objects.filter(
            condition__experiment=experiment, is_active=True
        ).exists()
        and experiment.questions.filter(
            section=Question.Section.STIMULUS
        ).exists()
    )


def readiness_problems(experiment: Experiment) -> list[str]:
    """Return reasons the study is not ready to go live (empty list = ready)."""
    problems: list[str] = []

    if not experiment.conditions.exists():
        problems.append("Add at least one condition.")

    active_stimuli = _active_stimuli(experiment)
    if not active_stimuli:
        problems.append("Add at least one active stimulus.")

    if not experiment.questions.filter(section=Question.Section.STIMULUS).exists():
        problems.append("Add at least one per-stimulus question.")

    if not (experiment.consent_text or "").strip():
        problems.append("Add the consent text shown before the study begins.")

    n = experiment.stimuli_per_participant
    if not experiment.is_pairwise and n and active_stimuli and n > len(active_stimuli):
        # In standard mode this is a per-participant stimulus count; in pairwise
        # mode it counts pairs, so the comparison doesn't apply there.
        problems.append(
            f"'Stimuli per participant' ({n}) exceeds the "
            f"{len(active_stimuli)} active stimuli available."
        )

    if experiment.is_pairwise and len(active_stimuli) < 2:
        problems.append("Pairwise studies need at least two stimuli to compare.")

    if experiment.eligibility_rule and not experiment.questions.filter(
        section=Question.Section.SCREENING
    ).exists():
        problems.append(
            "An eligibility rule is set but the study has no screening questions."
        )

    return problems
