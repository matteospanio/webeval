"""Upload an audio prompt to a draft experiment.

Mirrors the REST API at ``POST /api/v1/experiments/<slug>/prompts/``: takes
a local audio file and attaches it to the given ``(experiment, prompt_group)``
pair. Idempotent via SHA-256 — re-running with the same file reuses the
existing Prompt row.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from django.core.exceptions import ValidationError
from django.core.files import File
from django.core.management.base import BaseCommand, CommandError

from experiments.models import Experiment, Prompt


class Command(BaseCommand):
    help = "Upload an audio prompt and attach it to a prompt_group under a draft experiment."

    def add_arguments(self, parser):
        parser.add_argument("--experiment", required=True, help="Experiment slug.")
        parser.add_argument(
            "--prompt-group",
            required=True,
            help="Identifier linking continuation stimuli to this prompt.",
        )
        parser.add_argument("--audio", required=True, help="Path to the audio file.")
        parser.add_argument("--title", default="", help="Optional title.")
        parser.add_argument("--description", default="", help="Optional description.")

    def handle(self, *args, **options):
        slug = options["experiment"]
        try:
            experiment = Experiment.objects.get(slug=slug)
        except Experiment.DoesNotExist as exc:
            raise CommandError(f"No experiment with slug {slug!r}.") from exc

        if experiment.state != Experiment.State.DRAFT:
            raise CommandError(
                f"Experiment {slug!r} is {experiment.get_state_display().lower()}; "
                "prompts can only be added while it is in draft state."
            )

        path = Path(options["audio"])
        if not path.is_file():
            raise CommandError(f"Audio file not found: {path}")

        digest = _sha256(path)
        existing = Prompt.objects.filter(experiment=experiment, sha256=digest).first()
        if existing is not None:
            self.stdout.write(
                self.style.WARNING(
                    f"Skipped: a prompt with sha256 {digest} already exists "
                    f"(id={existing.pk}, prompt_group={existing.prompt_group!r})."
                )
            )
            return

        try:
            with path.open("rb") as fh:
                prompt = Prompt(
                    experiment=experiment,
                    prompt_group=options["prompt_group"],
                    title=options["title"],
                    description=options["description"],
                )
                prompt.audio.save(path.name, File(fh), save=False)
                prompt.full_clean()
                prompt.save()
        except ValidationError as exc:
            raise CommandError(f"Validation failed: {exc.messages}") from exc

        self.stdout.write(
            self.style.SUCCESS(
                f"Uploaded prompt id={prompt.pk} for experiment={slug!r}, "
                f"prompt_group={prompt.prompt_group!r}, sha256={prompt.sha256}, "
                f"duration_seconds={prompt.duration_seconds}."
            )
        )


def _sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            hasher.update(chunk)
    return hasher.hexdigest()
