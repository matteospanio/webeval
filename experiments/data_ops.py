"""Data-level operations on participant data for an experiment.

Shared helpers used by both the ``purge_experiment`` management command
and the admin "activate from test" confirmation view. Keeping the logic
here (instead of inline in each caller) ensures that both entry points
delete the same rows in the same order.
"""
from __future__ import annotations

from dataclasses import dataclass

from django.db import transaction

from experiments.models import Experiment
from survey.models import PairAssignment, ParticipantSession, Response, StimulusAssignment


@dataclass
class PurgeCounts:
    sessions: int
    assignments: int
    pair_assignments: int
    responses: int


def purge_participant_data(experiment: Experiment) -> PurgeCounts:
    """Delete every session/assignment/response row attached to ``experiment``.

    Runs inside a transaction so a partial wipe never leaks out. The
    experiment itself and its conditions/stimuli/questions are left
    untouched — this is a data reset, not a teardown.
    """
    with transaction.atomic():
        responses = Response.objects.filter(session__experiment=experiment)
        assignments = StimulusAssignment.objects.filter(
            session__experiment=experiment
        )
        pair_assignments = PairAssignment.objects.filter(
            session__experiment=experiment
        )
        sessions = ParticipantSession.objects.filter(experiment=experiment)

        counts = PurgeCounts(
            sessions=sessions.count(),
            assignments=assignments.count(),
            pair_assignments=pair_assignments.count(),
            responses=responses.count(),
        )

        # Delete in FK-dependency order: responses → assignments → sessions.
        responses.delete()
        assignments.delete()
        pair_assignments.delete()
        sessions.delete()

    return counts
