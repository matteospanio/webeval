"""Read-only admin registrations for participant data.

Staff users should be able to browse ParticipantSession and Response rows
from the Unfold sidebar. Both are deliberately registered as (essentially)
read-only: participant data is observational, and silently editing it from
the admin would corrupt downstream statistics.
"""
from __future__ import annotations

from django.contrib import admin
from unfold.admin import ModelAdmin as UnfoldModelAdmin

from .models import PairAssignment, ParticipantSession, Response, StimulusAssignment


@admin.register(ParticipantSession)
class ParticipantSessionAdmin(UnfoldModelAdmin):
    list_display = (
        "id",
        "experiment",
        "last_step",
        "started_at",
        "submitted_at",
        "country_code",
        "device_type",
    )
    list_filter = ("experiment", "last_step", "device_type")
    search_fields = ("id", "experiment__name", "country_code")
    readonly_fields = tuple(f.name for f in ParticipantSession._meta.fields)
    date_hierarchy = "started_at"

    def has_add_permission(self, request):
        return False


@admin.register(Response)
class ResponseAdmin(UnfoldModelAdmin):
    list_display = ("session", "question", "stimulus", "answered_at")
    list_filter = ("question__experiment", "question__section", "question__type")
    search_fields = ("session__id", "question__prompt", "answer_value")
    readonly_fields = tuple(f.name for f in Response._meta.fields)

    def has_add_permission(self, request):
        return False


@admin.register(StimulusAssignment)
class StimulusAssignmentAdmin(UnfoldModelAdmin):
    list_display = ("session", "stimulus", "sort_order", "listen_duration_ms")
    list_filter = ("stimulus__condition__experiment",)
    readonly_fields = tuple(f.name for f in StimulusAssignment._meta.fields)

    def has_add_permission(self, request):
        return False


@admin.register(PairAssignment)
class PairAssignmentAdmin(UnfoldModelAdmin):
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
