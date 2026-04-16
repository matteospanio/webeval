"""Delete all participant data for an experiment while preserving its config.

This is the GDPR / end-of-project data-deletion command. It wipes every
:class:`ParticipantSession`, its :class:`StimulusAssignment` rows, and all
:class:`Response` rows attached to the targeted experiment; the experiment
itself (and its conditions, stimuli, and questions) stay put so the study
remains reproducible from the reproducibility bundle alone.

The ``--yes`` flag is required: without it the command refuses to run and
exits with a non-zero status, so it's safe to drop into cron/CI by mistake.
"""
from __future__ import annotations

import sys

from django.core.management.base import BaseCommand, CommandError

from experiments.models import Experiment
from survey.models import ParticipantSession, Response, StimulusAssignment


class Command(BaseCommand):
    help = (
        "Delete all participant data (sessions, responses, listen times) for an "
        "experiment. The experiment itself and its conditions/stimuli/questions "
        "are preserved. Pass --yes to confirm."
    )

    def add_arguments(self, parser):
        parser.add_argument("slug", help="Slug of the experiment to purge.")
        parser.add_argument(
            "--yes",
            action="store_true",
            help="Required confirmation flag; without it the command aborts.",
        )

    def handle(self, *args, **options):
        slug = options["slug"]
        try:
            experiment = Experiment.objects.get(slug=slug)
        except Experiment.DoesNotExist as exc:
            raise CommandError(f"No experiment with slug {slug!r}.") from exc

        if not options["yes"]:
            self.stderr.write(
                "Refusing to purge participant data without the --yes flag."
            )
            sys.exit(1)

        responses = Response.objects.filter(session__experiment=experiment)
        assignments = StimulusAssignment.objects.filter(
            session__experiment=experiment
        )
        sessions = ParticipantSession.objects.filter(experiment=experiment)

        n_responses = responses.count()
        n_assignments = assignments.count()
        n_sessions = sessions.count()

        # Delete in FK-dependency order: responses → assignments → sessions.
        responses.delete()
        assignments.delete()
        sessions.delete()

        self.stdout.write(
            self.style.SUCCESS(
                f"Purged {n_sessions} sessions, {n_assignments} assignments, "
                f"{n_responses} responses for experiment {experiment.slug!r}."
            )
        )
