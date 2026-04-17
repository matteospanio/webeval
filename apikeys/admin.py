"""Read-only admin changelists for API keys + their audit events.

The self-service UI at ``/admin/api-keys/`` is where users manage their
own keys. These changelists are a superuser oversight surface: filter,
search, and inspect events across every user without leaving the standard
Django admin.
"""
from __future__ import annotations

from django.contrib import admin
from unfold.admin import ModelAdmin as UnfoldModelAdmin

from .models import APIKey, APIKeyEvent


@admin.register(APIKey)
class APIKeyAdmin(UnfoldModelAdmin):
    list_display = ("name", "user", "prefix", "status", "created_at", "last_used_at")
    list_filter = ("user",)
    search_fields = ("name", "prefix", "user__username")
    readonly_fields = (
        "id",
        "user",
        "name",
        "prefix",
        "hashed_key",
        "scopes",
        "created_at",
        "last_used_at",
        "expires_at",
        "revoked_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser


@admin.register(APIKeyEvent)
class APIKeyEventAdmin(UnfoldModelAdmin):
    list_display = (
        "created_at",
        "event_type",
        "user",
        "api_key",
        "request_method",
        "request_path",
        "response_status",
        "ip_address",
    )
    list_filter = ("event_type",)
    search_fields = (
        "user__username",
        "request_path",
        "ip_address",
        "api_key__name",
        "api_key__prefix",
    )
    readonly_fields = (
        "api_key",
        "user",
        "event_type",
        "created_at",
        "ip_address",
        "user_agent",
        "request_method",
        "request_path",
        "response_status",
        "detail",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
