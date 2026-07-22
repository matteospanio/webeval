"""List every installed webeval plugin (``manage.py plugins``)."""
from __future__ import annotations

from django.core.management.base import BaseCommand

from experiments.plugins import _KINDS, installed_plugins


class Command(BaseCommand):
    help = (
        "List installed webeval plugins: question components, assignment "
        "strategies, and pairwise strategies (built-ins included)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--kind",
            choices=sorted(spec.kind for spec in _KINDS),
            help="Only list plugins of this kind.",
        )

    def handle(self, *args, **options):
        rows = installed_plugins(options.get("kind"))
        if not rows:
            self.stdout.write("No plugins installed.")
            return
        header = ("KIND", "KEY", "LABEL", "IMPL", "ORIGIN")
        table = [
            (
                row.kind,
                row.key,
                row.label,
                row.impl,
                "built-in" if row.builtin else "third-party",
            )
            for row in sorted(rows, key=lambda r: (r.kind, r.key))
        ]
        widths = [
            max(len(cell) for cell in column) for column in zip(header, *table)
        ]
        for line in (header, *table):
            self.stdout.write(
                "  ".join(cell.ljust(w) for cell, w in zip(line, widths)).rstrip()
            )
