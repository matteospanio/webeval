"""DRF authentication class backed by the :class:`apikeys.models.APIKey` table.

Wire-compatible with ``rest_framework.authentication.TokenAuthentication``:
the ``Authorization: Token <key>`` header format is unchanged, so existing
scripts keep working as long as their ``WEBEVAL_API_TOKEN`` is regenerated
from the admin UI.

Every failure path writes an ``APIKeyEvent`` with ``event_type=auth_failed``
and a short reason code so admins can spot brute-force attempts and stale
scripts.
"""
from __future__ import annotations

from django.utils import timezone
from rest_framework import authentication, exceptions

from . import _request_meta
from .models import APIKey, APIKeyEvent, hash_key


class APIKeyAuthentication(authentication.BaseAuthentication):
    keyword = "Token"

    def authenticate(self, request):
        header = authentication.get_authorization_header(request).decode(
            "iso-8859-1", errors="replace"
        )
        if not header:
            return None  # no credentials offered → let anonymous handling run

        parts = header.split()
        if parts[0].lower() != self.keyword.lower():
            return None  # different auth scheme, not ours

        if len(parts) != 2:
            self._record_failure(request, None, "malformed")
            raise exceptions.AuthenticationFailed(
                "Invalid Authorization header. Expected 'Token <key>'."
            )

        raw = parts[1]
        try:
            api_key = APIKey.objects.select_related("user").get(
                hashed_key=hash_key(raw)
            )
        except APIKey.DoesNotExist:
            self._record_failure(request, None, "unknown")
            raise exceptions.AuthenticationFailed("Invalid API key.")

        if api_key.revoked_at is not None:
            self._record_failure(request, api_key, "revoked")
            raise exceptions.AuthenticationFailed("API key has been revoked.")

        if (
            api_key.expires_at is not None
            and api_key.expires_at <= timezone.now()
        ):
            self._record_failure(request, api_key, "expired")
            raise exceptions.AuthenticationFailed("API key has expired.")

        user = api_key.user
        if not user.is_active:
            self._record_failure(request, api_key, "user_inactive")
            raise exceptions.AuthenticationFailed("User inactive or deleted.")

        if not user.is_staff:
            self._record_failure(request, api_key, "user_not_staff")
            raise exceptions.AuthenticationFailed(
                "API key owner is not a staff user."
            )

        return (user, api_key)

    def authenticate_header(self, request):
        return self.keyword

    @staticmethod
    def _record_failure(request, api_key, reason: str) -> None:
        meta = _request_meta.extract(request)
        APIKeyEvent.objects.create(
            api_key=api_key,
            user=api_key.user if api_key is not None else None,
            event_type=APIKeyEvent.Event.AUTH_FAILED,
            ip_address=meta.ip_address,
            user_agent=meta.user_agent,
            request_method=(request.method or "")[:10],
            request_path=(request.path or "")[:500],
            detail={"reason": reason},
        )
