"""Read-only admin registrations for participant data.

Staff users should be able to browse ParticipantSession and Response rows
from the Unfold sidebar. Both are deliberately registered as (essentially)
read-only: participant data is observational, and silently editing it from
the admin would corrupt downstream statistics.
"""
from __future__ import annotations

from django.contrib import admin
from unfold.admin import ModelAdmin as UnfoldModelAdmin

from accounts.admin_mixins import OwnerScopedAdminMixin

from .models import (
    PairAssignment,
    ParticipantSession,
    Response,
    StimulusAssignment,
    SurveyEvent,
)


class FlaggedFilter(admin.SimpleListFilter):
    title = "flagged"
    parameter_name = "flagged"

    def lookups(self, request, model_admin):
        return (("yes", "Flagged"), ("no", "Not flagged"))

    def queryset(self, request, queryset):
        if self.value() == "yes":
            return queryset.exclude(flags=[])
        if self.value() == "no":
            return queryset.filter(flags=[])
        return queryset


@admin.register(ParticipantSession)
class ParticipantSessionAdmin(OwnerScopedAdminMixin, UnfoldModelAdmin):
    experiment_lookup = "experiment"
    list_display = (
        "id",
        "experiment",
        "last_step",
        "submitted_at",
        "is_preview",
        "failed_attention_checks",
        "flag_list",
        "external_id",
        "completion_code",
        "compensation_status",
    )
    list_editable = ("compensation_status",)
    list_filter = (
        "experiment",
        "last_step",
        "is_preview",
        "device_type",
        "compensation_status",
        FlaggedFilter,
    )
    search_fields = (
        "id",
        "experiment__name",
        "country_code",
        "external_id",
        "completion_code",
    )
    # Everything is read-only except compensation_status, which researchers
    # update as they reconcile payments.
    readonly_fields = tuple(
        f.name
        for f in ParticipantSession._meta.fields
        if f.name != "compensation_status"
    )
    date_hierarchy = "started_at"

    @admin.display(description="Flags")
    def flag_list(self, obj):
        return ", ".join(obj.flags) if obj.flags else "—"

    def has_add_permission(self, request):
        return False


@admin.register(Response)
class ResponseAdmin(OwnerScopedAdminMixin, UnfoldModelAdmin):
    experiment_lookup = "session__experiment"
    list_display = ("session", "question", "stimulus", "answered_at")
    list_filter = ("question__experiment", "question__section", "question__type")
    search_fields = ("session__id", "question__prompt", "answer_value")
    readonly_fields = tuple(f.name for f in Response._meta.fields)

    def has_add_permission(self, request):
        return False


@admin.register(StimulusAssignment)
class StimulusAssignmentAdmin(OwnerScopedAdminMixin, UnfoldModelAdmin):
    experiment_lookup = "session__experiment"
    list_display = ("session", "stimulus", "sort_order", "listen_duration_ms")
    list_filter = ("stimulus__condition__experiment",)
    readonly_fields = tuple(f.name for f in StimulusAssignment._meta.fields)

    def has_add_permission(self, request):
        return False


@admin.register(PairAssignment)
class PairAssignmentAdmin(OwnerScopedAdminMixin, UnfoldModelAdmin):
    experiment_lookup = "session__experiment"
    list_display = (
        "session",
        "stimulus_a",
        "stimulus_b",
        "prompt_group",
        "position_a",
        "sort_order",
        "listen_duration_a_ms",
        "listen_duration_b_ms",
    )
    list_filter = ("session__experiment",)
    readonly_fields = tuple(f.name for f in PairAssignment._meta.fields)

    def has_add_permission(self, request):
        return False


@admin.register(SurveyEvent)
class SurveyEventAdmin(OwnerScopedAdminMixin, UnfoldModelAdmin):
    experiment_lookup = "session__experiment"
    list_display = ("session", "event_type", "label", "elapsed_ms", "created_at")
    list_filter = ("event_type",)
    search_fields = ("session__id", "label")
    readonly_fields = tuple(f.name for f in SurveyEvent._meta.fields)
    date_hierarchy = "created_at"

    def has_add_permission(self, request):
        return False
