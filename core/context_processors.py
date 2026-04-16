"""Template context processors for the webeval project.

``admin_summary`` injects a :class:`experiments.stats.GlobalSummary` into
the template context for every admin page rendered to a staff user. The
admin index template reads ``webeval_summary`` to render the summary
cards at the top of the page.
"""
from __future__ import annotations

from typing import Any


def admin_summary(request) -> dict[str, Any]:
    # Only compute when we're actually rendering an admin page to a staff
    # user — otherwise the extra queries are pure waste.
    if not request.path.startswith("/admin/"):
        return {}
    user = getattr(request, "user", None)
    if user is None or not getattr(user, "is_staff", False):
        return {}
    # Import lazily to avoid app-loading order issues.
    from experiments.stats import global_summary

    try:
        return {"webeval_summary": global_summary()}
    except Exception:
        # Never let a stats helper break the admin.
        return {}
