"""Delete participant data past each experiment's retention window.

For every experiment with ``retention_days > 0``, deletes sessions older than
that window — completed sessions measured from ``submitted_at``, abandoned ones
from ``started_at`` — cascading to their responses, assignments, and events.
The experiment's configuration is untouched. Each sweep is recorded as an
``AuditEvent``. Run it from cron/CI; ``--dry-run`` reports without deleting.
"""
from __future__ import annotations

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone

from accounts.models import AuditEvent
from accounts.services import record_audit
from experiments.models import Experiment
from survey.models import ParticipantSession


class Command(BaseCommand):
    help = "Delete participant data past each experiment's retention window."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would be deleted without deleting anything.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        now = timezone.now()
        total = 0
        for experiment in Experiment.objects.filter(retention_days__gt=0):
            cutoff = now - timedelta(days=experiment.retention_days)
            expired = ParticipantSession.objects.filter(experiment=experiment).filter(
                Q(submitted_at__isnull=False, submitted_at__lt=cutoff)
                | Q(submitted_at__isnull=True, started_at__lt=cutoff)
            )
            count = expired.count()
            if not count:
                continue
            total += count
            if dry_run:
                self.stdout.write(
                    f"[dry-run] {experiment.slug}: would delete {count} session(s) "
                    f"older than {experiment.retention_days} days"
                )
                continue
            expired.delete()  # cascades to responses / assignments / events
            record_audit(
                experiment, AuditEvent.Action.DELETE, target="retention",
                sessions=count, retention_days=experiment.retention_days,
            )
            self.stdout.write(
                f"{experiment.slug}: deleted {count} session(s) past retention"
            )
        self.stdout.write(
            self.style.SUCCESS(
                f"Retention sweep complete — {total} session(s) "
                f"{'would be ' if dry_run else ''}removed."
            )
        )
