"""Identity + per-study access-control models.

This app sits between ``django.contrib.auth`` and the ``experiments`` domain:

* ``Profile`` — a 1:1 extension of the stock ``User``. We deliberately do *not*
  swap ``AUTH_USER_MODEL`` (a risky one-way migration over existing rows + FKs);
  a Profile carries the long-roadmap fields at a fraction of the risk.
* ``Membership`` — grants a user a role (owner/editor/viewer) on one
  experiment. The experiment owner also holds an ``OWNER`` membership so every
  permission check is a single uniform query.
* ``Invitation`` — a single-use, expiring, tokenised invite to collaborate.
* ``AccessEvent`` — append-only audit log of every access-control change,
  mirroring :class:`apikeys.models.APIKeyEvent`.

``Membership`` / ``Invitation`` / ``AccessEvent`` reference
``experiments.Experiment`` by string so the ``experiments`` app never imports
``accounts`` at module-load time (the dependency graph stays one-directional:
``studio → accounts → experiments``).

FUTURE — Organization / multi-tenant seam: an ``Organization`` model would own a
nullable ``Experiment.organization`` FK and an ``OrgMembership(user, org, role)``
reusing :class:`accounts.roles.Role`. :func:`accounts.permissions.role_for`
would fall back to the org role when no study-level ``Membership`` exists.
Nothing in this module needs to change to add that layer.
"""
from __future__ import annotations

import secrets
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone

from .roles import Role

INVITATION_TOKEN_BYTES = 32
INVITATION_TTL_DAYS = 14


def _default_invitation_expiry():
    return timezone.now() + timedelta(days=INVITATION_TTL_DAYS)


def _new_invitation_token() -> str:
    return secrets.token_urlsafe(INVITATION_TOKEN_BYTES)


class Profile(models.Model):
    class GlobalRole(models.TextChoices):
        RESEARCHER = "researcher", "Researcher"
        PLATFORM_ADMIN = "platform_admin", "Platform admin"

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="profile",
    )
    display_name = models.CharField(max_length=150, blank=True)
    preferred_language = models.CharField(
        max_length=10,
        blank=True,
        help_text=(
            "BCP-47 language tag (e.g. 'en', 'it'). Reserved for multilingual "
            "content; not yet enforced."
        ),
    )
    global_role = models.CharField(
        max_length=20,
        choices=GlobalRole.choices,
        default=GlobalRole.RESEARCHER,
        help_text=(
            "Platform-level role, distinct from Django's is_superuser. "
            "Reserved for future platform-admin tooling."
        ),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.display_name or self.user.get_username()

    @property
    def name(self) -> str:
        return self.display_name or self.user.get_username()


class Membership(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="memberships",
    )
    experiment = models.ForeignKey(
        "experiments.Experiment",
        on_delete=models.CASCADE,
        related_name="memberships",
    )
    role = models.CharField(max_length=16, choices=Role.choices)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )

    class Meta:
        ordering = ("experiment_id", "role", "user_id")
        constraints = [
            models.UniqueConstraint(
                fields=["user", "experiment"],
                name="uniq_membership_user_experiment",
            )
        ]

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"{self.user} · {self.role} · experiment {self.experiment_id}"


class Invitation(models.Model):
    experiment = models.ForeignKey(
        "experiments.Experiment",
        on_delete=models.CASCADE,
        related_name="invitations",
    )
    email = models.EmailField()
    role = models.CharField(
        max_length=16, choices=Role.choices, default=Role.EDITOR
    )
    # Stored in plaintext (unlike the hashed APIKey): the token is single-use,
    # short-lived, and delivered out-of-band by email/copy-link, so the lookup
    # by raw token is intentional and matches common Django invite practice.
    token = models.CharField(
        max_length=64,
        unique=True,
        db_index=True,
        default=_new_invitation_token,
        editable=False,
    )
    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(default=_default_invitation_expiry)
    accepted_at = models.DateTimeField(null=True, blank=True)
    accepted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )

    class Meta:
        ordering = ("-created_at",)
        indexes = [models.Index(fields=["experiment", "email"])]

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"{self.email} → experiment {self.experiment_id} ({self.role})"

    def is_expired(self) -> bool:
        return self.expires_at is not None and self.expires_at <= timezone.now()

    def is_pending(self) -> bool:
        return self.accepted_at is None and not self.is_expired()

    @property
    def status(self) -> str:
        if self.accepted_at is not None:
            return "accepted"
        if self.is_expired():
            return "expired"
        return "pending"

    def mark_accepted(self, user) -> None:
        self.accepted_at = timezone.now()
        self.accepted_by = user
        self.save(update_fields=["accepted_at", "accepted_by"])


class AccessEvent(models.Model):
    """Append-only audit log of access-control changes (mirrors APIKeyEvent)."""

    class Event(models.TextChoices):
        INVITE_SENT = "invite_sent", "Invitation sent"
        INVITE_ACCEPTED = "invite_accepted", "Invitation accepted"
        INVITE_REVOKED = "invite_revoked", "Invitation revoked"
        ROLE_CHANGED = "role_changed", "Role changed"
        MEMBER_REMOVED = "member_removed", "Member removed"
        OWNERSHIP_TRANSFERRED = "ownership_transferred", "Ownership transferred"

    experiment = models.ForeignKey(
        "experiments.Experiment",
        on_delete=models.CASCADE,
        related_name="access_events",
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    target_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    event_type = models.CharField(
        max_length=24, choices=Event.choices, db_index=True
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=500, blank=True)
    detail = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self) -> str:  # pragma: no cover - trivial
        when = self.created_at.isoformat() if self.created_at else "?"
        return f"{self.event_type} @ {when}"
