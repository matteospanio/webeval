"""Single source of truth for study-level access decisions.

Used identically by the studio dashboard views (gate + 403/404), the Django
admin (:class:`accounts.admin_mixins.OwnerScopedAdminMixin`) and the REST API
(object-level checks in :mod:`experiments.api`).

A superuser bypasses all checks. The experiment owner and any ``Membership``
holder get a role; everyone else gets ``None``.
"""
from __future__ import annotations

from .models import Membership
from .roles import ROLE_RANK, Role


def role_for(user, experiment) -> str | None:
    """Return the user's effective role on ``experiment``, or ``None``."""
    if user is None or not getattr(user, "is_authenticated", False):
        return None
    if user.is_superuser:
        return Role.OWNER
    if getattr(experiment, "owner_id", None) == user.pk:
        return Role.OWNER
    role = (
        Membership.objects.filter(user=user, experiment=experiment)
        .values_list("role", flat=True)
        .first()
    )
    if role is not None:
        return role
    # Gradual RBAC rollout: experiments created before the ownership model
    # (``owner IS NULL``) keep the pre-RBAC behaviour where any staff user
    # manages any study. As soon as an experiment has an owner, access is
    # scoped strictly to its owner + collaborators (+ superusers). New studies
    # created via studio/admin/import always get an owner, so this fallback
    # only ever applies to legacy, unclaimed rows.
    if experiment.owner_id is None and getattr(user, "is_staff", False):
        return Role.OWNER
    return None


def _has_rank(user, experiment, minimum: str) -> bool:
    role = role_for(user, experiment)
    if role is None:
        return False
    return ROLE_RANK[role] >= ROLE_RANK[minimum]


def can_view(user, experiment) -> bool:
    """View results, exports and charts (owner, editor or viewer)."""
    return role_for(user, experiment) is not None


def can_edit(user, experiment) -> bool:
    """Structural authoring + stimulus/prompt uploads (owner or editor)."""
    return _has_rank(user, experiment, Role.EDITOR)


def can_manage(user, experiment) -> bool:
    """Collaborators, ownership transfer and lifecycle (owner only)."""
    return _has_rank(user, experiment, Role.OWNER)


def visible_experiment_ids(user) -> set[int]:
    """Ids of experiments a user may view (owned ∪ collaborating).

    Superusers are unrestricted; callers short-circuit before calling this.
    The ``experiments`` import is function-local to keep this module free of a
    module-load dependency on the domain app.
    """
    from experiments.models import Experiment

    owned = Experiment.objects.filter(owner=user).values_list("id", flat=True)
    member = Membership.objects.filter(user=user).values_list(
        "experiment_id", flat=True
    )
    ids = set(owned) | set(member)
    # Mirror role_for: legacy unowned experiments stay visible to staff so the
    # admin changelist and the object-level checks agree.
    if getattr(user, "is_staff", False):
        ids |= set(
            Experiment.objects.filter(owner__isnull=True).values_list(
                "id", flat=True
            )
        )
    return ids
