"""Admin registrations for identity + access.

* The stock ``User``/``Group`` admins are re-registered through Unfold so the
  "control user privileges" surface (is_staff/is_active/groups/permissions)
  matches the rest of the themed admin. A ``Profile`` inline rides along.
* ``Membership`` / ``Invitation`` / ``AccessEvent`` are superuser-oversight
  changelists; day-to-day management happens in the studio dashboard, and the
  audit log is append-only (mirrors ``apikeys`` admin).
"""
from __future__ import annotations

from django.contrib import admin
from django.contrib.auth.admin import GroupAdmin as BaseGroupAdmin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import Group, User
from unfold.admin import ModelAdmin as UnfoldModelAdmin
from unfold.admin import StackedInline as UnfoldStackedInline

from .models import AccessEvent, AuditEvent, Invitation, Membership, Profile

admin.site.unregister(User)
admin.site.unregister(Group)


class ProfileInline(UnfoldStackedInline):
    model = Profile
    can_delete = False
    extra = 0
    verbose_name_plural = "Profile"
    fields = ("display_name", "preferred_language", "global_role")


@admin.register(User)
class UserAdmin(BaseUserAdmin, UnfoldModelAdmin):
    inlines = (ProfileInline,)


@admin.register(Group)
class GroupAdmin(BaseGroupAdmin, UnfoldModelAdmin):
    pass


@admin.register(Membership)
class MembershipAdmin(UnfoldModelAdmin):
    list_display = ("user", "experiment", "role", "created_at", "created_by")
    list_filter = ("role",)
    search_fields = ("user__username", "experiment__name", "experiment__slug")
    readonly_fields = ("created_at",)
    autocomplete_fields = ("user", "experiment", "created_by")

    def has_add_permission(self, request):
        return request.user.is_superuser

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser


@admin.register(Invitation)
class InvitationAdmin(UnfoldModelAdmin):
    list_display = (
        "email",
        "experiment",
        "role",
        "status",
        "created_at",
        "expires_at",
        "invited_by",
    )
    list_filter = ("role",)
    search_fields = ("email", "experiment__name", "experiment__slug")
    readonly_fields = tuple(f.name for f in Invitation._meta.fields) + ("status",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser


@admin.register(AccessEvent)
class AccessEventAdmin(UnfoldModelAdmin):
    list_display = (
        "created_at",
        "event_type",
        "experiment",
        "actor",
        "target_user",
        "ip_address",
    )
    list_filter = ("event_type",)
    search_fields = (
        "experiment__name",
        "experiment__slug",
        "actor__username",
        "target_user__username",
    )
    readonly_fields = tuple(f.name for f in AccessEvent._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(AuditEvent)
class AuditEventAdmin(UnfoldModelAdmin):
    list_display = ("created_at", "action", "target", "experiment", "actor", "ip_address")
    list_filter = ("action",)
    search_fields = ("experiment__name", "experiment__slug", "actor__username", "target")
    readonly_fields = tuple(f.name for f in AuditEvent._meta.fields)
    date_hierarchy = "created_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
