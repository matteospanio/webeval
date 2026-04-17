"""Scope-check permission factory for DRF views.

Views opt in to a scope with

    permission_classes = [HasScope("stimuli:upload")]

The returned class verifies two invariants: the authenticated user is
staff, and ``request.auth`` is an :class:`APIKey` carrying the required
scope.
"""
from __future__ import annotations

from rest_framework import permissions

from .models import APIKey


def HasScope(scope: str) -> type[permissions.BasePermission]:
    class _HasScope(permissions.BasePermission):
        message = f"API key is missing the required scope: {scope}"

        def has_permission(self, request, view) -> bool:
            user = getattr(request, "user", None)
            if user is None or not user.is_authenticated or not user.is_staff:
                return False
            api_key = getattr(request, "auth", None)
            if not isinstance(api_key, APIKey):
                return False
            return scope in (api_key.scopes or [])

    _HasScope.__name__ = f"HasScope_{scope.replace(':', '_').replace('-', '_')}"
    return _HasScope
