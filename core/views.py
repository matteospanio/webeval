"""Project-level admin views.

At the moment there is exactly one: a staff-only endpoint that streams a
Django ``dumpdata`` JSON of the whole database, so admins can grab a full
backup from the UI without shelling into the host.
"""
from __future__ import annotations

import io
from datetime import datetime

from django.contrib.admin.views.decorators import staff_member_required
from django.core.management import call_command
from django.http import HttpResponse
from django.views.decorators.http import require_GET


# ``contenttypes`` and ``auth.permission`` are recreated on every ``migrate``
# from model metadata, so excluding them here keeps the dump portable
# across fresh databases. Sessions are transient cookies — useless in a
# backup, and noisy.
_EXCLUDED_APPS_MODELS = (
    "contenttypes",
    "auth.permission",
    "sessions",
    "admin.logentry",
)


@require_GET
@staff_member_required
def database_export(request) -> HttpResponse:
    """Return a ``dumpdata`` JSON export of the whole project database.

    Uses Django's built-in serializer so the output can be restored with
    ``loaddata`` on any supported DB backend — not just the SQLite file
    we ship with.
    """
    buffer = io.StringIO()
    call_command(
        "dumpdata",
        *[f"--exclude={name}" for name in _EXCLUDED_APPS_MODELS],
        "--natural-foreign",
        "--natural-primary",
        "--indent=2",
        stdout=buffer,
    )
    payload = buffer.getvalue().encode("utf-8")
    filename = "webeval-db-{}.json".format(datetime.utcnow().strftime("%Y%m%d-%H%M%S"))
    response = HttpResponse(payload, content_type="application/json")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    response["Content-Length"] = str(len(payload))
    return response
