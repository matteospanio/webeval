"""Django system checks for the plugin system.

Registered from :meth:`ExperimentsConfig.ready`. The one check here catches
the "orphaned type" failure mode: questions were authored with a plugin
question type, and the plugin app was later removed (or its key renamed), so
the stored ``Question.type`` no longer matches any registered component and
the widget cannot render. ``readiness_problems`` blocks *activating* such a
study; this check also surfaces the problem on every ``manage.py check
--database default`` / ``migrate`` for studies that are already live.
"""
from __future__ import annotations

from django.core import checks


@checks.register(checks.Tags.database)
def check_orphaned_question_types(app_configs, databases=None, **kwargs):
    if not databases:
        return []

    from django.db.utils import DatabaseError

    from experiments.components import BUILTIN_TYPES, is_question_component
    from experiments.models import Question

    try:
        stored = set(Question.objects.values_list("type", flat=True).distinct())
    except DatabaseError:
        # Unmigrated / unavailable database — nothing to check yet.
        return []

    orphaned = sorted(
        t for t in stored if t not in BUILTIN_TYPES and not is_question_component(t)
    )
    if not orphaned:
        return []
    return [
        checks.Warning(
            "Stored questions reference question types with no registered "
            f"component: {', '.join(orphaned)}.",
            hint=(
                "A plugin app was probably removed from INSTALLED_APPS (or "
                "renamed its type key) after studies were authored with it. "
                "Re-install the plugin or migrate the affected questions — "
                "participants cannot answer a question whose widget cannot "
                "render."
            ),
            id="experiments.W001",
        )
    ]
