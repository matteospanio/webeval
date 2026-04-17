"""Shared request-metadata helpers for API-key event logging.

The IP + user-agent extraction logic mirrors ``survey/metadata.py`` but is
split out so ``apikeys`` does not import from ``survey``. Only the bits
needed for audit rows are kept here — no GeoIP lookup (the admin doesn't
need country codes for key-usage events).
"""
from __future__ import annotations

from dataclasses import dataclass

from django.http import HttpRequest


@dataclass
class RequestMeta:
    ip_address: str | None
    user_agent: str


def extract(request: HttpRequest) -> RequestMeta:
    return RequestMeta(
        ip_address=_client_ip(request),
        user_agent=(request.META.get("HTTP_USER_AGENT", "") or "")[:500],
    )


def _client_ip(request: HttpRequest) -> str | None:
    xff = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if xff:
        return xff.split(",")[0].strip() or None
    return request.META.get("REMOTE_ADDR") or None
