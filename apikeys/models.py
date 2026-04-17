"""Per-user scoped API keys + append-only audit log.

Replaces ``rest_framework.authtoken`` with a self-service model:

* Each staff user may hold many active ``APIKey`` rows, each scoped to a
  subset of ``apikeys.scopes.SCOPES``, rotatable and revocable from the
  admin UI at ``/admin/api-keys/``.
* Raw keys are never stored — only the SHA-256 hex digest. The plaintext
  is returned once at generation and shown to the user on a one-shot page.
* Every lifecycle event (create/rotate/revoke), every successful API use,
  and every authentication failure is recorded in ``APIKeyEvent``.
"""
from __future__ import annotations

import hashlib
import secrets
import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone

from .scopes import SCOPES

KEY_PREFIX_LENGTH = 8
RAW_KEY_PREFIX = "webeval_"


def hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class APIKey(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="api_keys",
    )
    name = models.CharField(max_length=100)
    prefix = models.CharField(max_length=16, unique=True, db_index=True)
    hashed_key = models.CharField(max_length=64, unique=True, db_index=True)
    scopes = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at",)
        verbose_name = "API key"
        verbose_name_plural = "API keys"

    def __str__(self) -> str:
        return f"{self.name} ({self.prefix}…)"

    @classmethod
    def generate(
        cls,
        *,
        user,
        name: str,
        scopes,
        expires_at=None,
    ) -> tuple["APIKey", str]:
        """Create a new key and return ``(instance, raw_key)``.

        The plaintext ``raw_key`` is shown to the user once and then
        discarded — only its SHA-256 digest is persisted.
        """
        random_part = secrets.token_urlsafe(32)
        raw = RAW_KEY_PREFIX + random_part
        instance = cls.objects.create(
            user=user,
            name=name,
            scopes=list(scopes),
            prefix=random_part[:KEY_PREFIX_LENGTH],
            hashed_key=hash_key(raw),
            expires_at=expires_at,
        )
        return instance, raw

    def is_active(self) -> bool:
        if self.revoked_at is not None:
            return False
        if self.expires_at is not None and self.expires_at <= timezone.now():
            return False
        return True

    @property
    def status(self) -> str:
        if self.revoked_at is not None:
            return "revoked"
        if self.expires_at is not None and self.expires_at <= timezone.now():
            return "expired"
        return "active"

    def scope_labels(self) -> list[str]:
        return [SCOPES.get(s, s) for s in (self.scopes or [])]


class APIKeyEvent(models.Model):
    class Event(models.TextChoices):
        CREATED = "created", "Created"
        ROTATED = "rotated", "Rotated"
        REVOKED = "revoked", "Revoked"
        USED = "used", "Used"
        AUTH_FAILED = "auth_failed", "Auth failed"

    api_key = models.ForeignKey(
        APIKey,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="events",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    event_type = models.CharField(
        max_length=20, choices=Event.choices, db_index=True
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=500, blank=True)
    request_method = models.CharField(max_length=10, blank=True)
    request_path = models.CharField(max_length=500, blank=True)
    response_status = models.PositiveSmallIntegerField(null=True, blank=True)
    detail = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ("-created_at",)
        verbose_name = "API key event"
        verbose_name_plural = "API key events"

    def __str__(self) -> str:
        when = self.created_at.isoformat() if self.created_at else "?"
        return f"{self.event_type} @ {when}"
