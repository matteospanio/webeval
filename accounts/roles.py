"""Study-level collaboration roles.

Kept in a standalone module with no model imports so a future Organization /
multi-tenant layer can reuse the same ``Role`` enum without importing the
study-level access models. See :mod:`accounts.models` for the documented
Organization seam.
"""
from __future__ import annotations

from django.db import models


class Role(models.TextChoices):
    OWNER = "owner", "Owner"
    EDITOR = "editor", "Editor"
    VIEWER = "viewer", "Viewer"


# Higher rank ⇒ more capability. Used for ``>=``-style comparisons.
ROLE_RANK: dict[str, int] = {
    Role.VIEWER: 1,
    Role.EDITOR: 2,
    Role.OWNER: 3,
}
