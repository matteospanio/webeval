"""Django admin registrations for the experiments app.

Configuration is centred on the Experiment changelist: admins create a draft
Experiment, add Conditions, Stimuli, and Questions inline, then flip the
experiment to ``active``. Once active, structural inlines become read-only
because the model-level ``_ensure_draft`` guard would reject any write.

The Experiment change view also embeds live participation statistics (a
``live_stats`` readonly field) and links to per-experiment admin views —
the details page, CSV exports, and SVG chart are all mounted under
``/admin/experiments/experiment/<slug>/…`` via ``get_urls()`` below, so
there is no separate "dashboard" app.
"""
from __future__ import annotations

from django.contrib import admin, messages
from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.urls import path, reverse
from django.utils.html import format_html
from unfold.admin import ModelAdmin as UnfoldModelAdmin
from unfold.admin import TabularInline as UnfoldTabularInline

from .charts import bradley_terry_svg, mean_ratings_svg, pairwise_win_rates_svg
from .csv_exports import (
    answers_csv_response,
    demographics_csv_response,
    pairwise_answers_csv_response,
)
from .forms import QuestionAdminForm
from .models import Condition, Experiment, Question, Stimulus
from .stats import (
    bradley_terry_analysis,
    experiment_counts,
    mean_listen_duration_ms,
    pairwise_experiment_stats,
    per_stimulus_mean_ratings,
)


class _ReadOnlyWhenLockedMixin:
    """Inline helper: make the inline read-only once its parent leaves draft."""

    def _parent_is_draft(self, request, obj) -> bool:
        if obj is None:  # creation page
            return True
        experiment = obj if isinstance(obj, Experiment) else getattr(obj, "experiment", None)
        if experiment is None and hasattr(obj, "condition"):
            experiment = obj.condition.experiment
        return experiment is None or experiment.state == Experiment.State.DRAFT

    def has_add_permission(self, request, obj=None):
        return self._parent_is_draft(request, obj) and super().has_add_permission(request, obj)

    def has_change_permission(self, request, obj=None):
        # Let users view rows but not save edits when locked.
        if not self._parent_is_draft(request, obj):
            return False
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        if not self._parent_is_draft(request, obj):
            return False
        return super().has_delete_permission(request, obj)


class ConditionInline(_ReadOnlyWhenLockedMixin, UnfoldTabularInline):
    model = Condition
    extra = 0
    fields = ("name", "description")


class QuestionInline(_ReadOnlyWhenLockedMixin, UnfoldTabularInline):
    model = Question
    extra = 0
    # Keep the inline lean — per-type config is edited on the Question
    # changeform, where QuestionAdminForm renders flat helper fields
    # instead of raw JSON.
    fields = ("section", "type", "prompt", "required", "page_break_before", "show_prompt", "sort_order")
    show_change_link = True


@admin.action(description="Export reproducibility bundle (JSON)")
def export_repro_json(modeladmin, request, queryset):
    if queryset.count() != 1:
        modeladmin.message_user(
            request,
            "Select exactly one experiment to export.",
            level=messages.ERROR,
        )
        return None
    exp = queryset.first()
    return HttpResponseRedirect(
        reverse("experiments:repro_json", kwargs={"slug": exp.slug})
    )


@admin.action(description="Open printable study document")
def open_printable(modeladmin, request, queryset):
    if queryset.count() != 1:
        modeladmin.message_user(
            request,
            "Select exactly one experiment to view.",
            level=messages.ERROR,
        )
        return None
    exp = queryset.first()
    return HttpResponseRedirect(
        reverse("experiments:printable", kwargs={"slug": exp.slug})
    )


@admin.register(Experiment)
class ExperimentAdmin(UnfoldModelAdmin):
    list_display = ("name", "slug", "state", "mode", "assignment_strategy", "created_at", "shortcuts")
    list_filter = ("state", "mode", "assignment_strategy")
    search_fields = ("name", "slug", "description")
    prepopulated_fields = {"slug": ("name",)}
    inlines = (ConditionInline, QuestionInline)
    actions = (export_repro_json, open_printable)
    readonly_fields = ("live_stats",)

    fieldsets = (
        (None, {"fields": ("name", "slug", "state", "mode", "description")}),
        (
            "Participant flow",
            {
                "fields": (
                    "consent_text",
                    "instructions_content",
                    "thanks_content",
                    "privacy_contact",
                    "privacy_policy_url",
                    "stimuli_per_participant",
                    "assignment_strategy",
                    "require_audio_check",
                )
            },
        ),
        (
            "Statistics",
            {
                "description": (
                    "Live counts for this experiment. Blank on a brand-new "
                    "draft; populated as soon as participants start the survey."
                ),
                "fields": ("live_stats",),
            },
        ),
    )

    def get_fieldsets(self, request, obj=None):
        # On the add form there is no pk yet, so stats would be misleading.
        if obj is None:
            return (
                (None, {"fields": ("name", "slug", "state", "mode", "description")}),
                (
                    "Participant flow",
                    {
                        "fields": (
                            "consent_text",
                            "instructions_content",
                            "thanks_content",
                            "privacy_contact",
                            "privacy_policy_url",
                            "stimuli_per_participant",
                            "assignment_strategy",
                        )
                    },
                ),
            )
        return super().get_fieldsets(request, obj)

    def get_urls(self):
        """Mount per-experiment detail, CSV, and chart views under the admin.

        The URLs are namespaced by Django's admin site, so their reverse
        names are ``admin:experiments_experiment_details`` /
        ``_answers_csv`` / ``_demographics_csv`` /
        ``_chart_mean_ratings``. This replaces the old standalone
        ``dashboard`` app entirely.
        """
        urls = super().get_urls()
        custom = [
            path(
                "<slug:slug>/details/",
                self.admin_site.admin_view(self.experiment_details_view),
                name="experiments_experiment_details",
            ),
            path(
                "<slug:slug>/answers.csv",
                self.admin_site.admin_view(self.answers_csv_view),
                name="experiments_experiment_answers_csv",
            ),
            path(
                "<slug:slug>/demographics.csv",
                self.admin_site.admin_view(self.demographics_csv_view),
                name="experiments_experiment_demographics_csv",
            ),
            path(
                "<slug:slug>/chart/mean-ratings.svg",
                self.admin_site.admin_view(self.chart_mean_ratings_view),
                name="experiments_experiment_chart_mean_ratings",
            ),
            path(
                "<slug:slug>/pairwise-answers.csv",
                self.admin_site.admin_view(self.pairwise_answers_csv_view),
                name="experiments_experiment_pairwise_answers_csv",
            ),
            path(
                "<slug:slug>/chart/pairwise-wins.svg",
                self.admin_site.admin_view(self.chart_pairwise_wins_view),
                name="experiments_experiment_chart_pairwise_wins",
            ),
            path(
                "<slug:slug>/chart/bt-scores.svg",
                self.admin_site.admin_view(self.chart_bt_scores_view),
                name="experiments_experiment_chart_bt_scores",
            ),
        ]
        # Custom routes must come before the generic ``<path:object_id>/``
        # entry Django registers for change/delete views, otherwise the
        # slug gets swallowed by the object-id matcher.
        return custom + urls

    def experiment_details_view(self, request, slug: str):
        experiment = get_object_or_404(Experiment, slug=slug)
        context = {
            **self.admin_site.each_context(request),
            "experiment": experiment,
            "counts": experiment_counts(experiment),
            "mean_listen_ms": mean_listen_duration_ms(experiment),
        }
        if experiment.mode == Experiment.Mode.PAIRWISE:
            context["pairwise_stats"] = pairwise_experiment_stats(experiment)
            context["bt_stats"] = bradley_terry_analysis(experiment)
            context["bt_chart_svg"] = bradley_terry_svg(experiment)
        else:
            context["per_stimulus"] = per_stimulus_mean_ratings(experiment)
            context["chart_svg"] = mean_ratings_svg(experiment)
        return render(request, "admin/experiments/experiment/details.html", context)

    def answers_csv_view(self, request, slug: str):
        experiment = get_object_or_404(Experiment, slug=slug)
        return answers_csv_response(experiment)

    def demographics_csv_view(self, request, slug: str):
        experiment = get_object_or_404(Experiment, slug=slug)
        return demographics_csv_response(experiment)

    def chart_mean_ratings_view(self, request, slug: str):
        experiment = get_object_or_404(Experiment, slug=slug)
        return HttpResponse(
            mean_ratings_svg(experiment), content_type="image/svg+xml"
        )

    def pairwise_answers_csv_view(self, request, slug: str):
        experiment = get_object_or_404(Experiment, slug=slug)
        return pairwise_answers_csv_response(experiment)

    def chart_pairwise_wins_view(self, request, slug: str):
        experiment = get_object_or_404(Experiment, slug=slug)
        return HttpResponse(
            pairwise_win_rates_svg(experiment), content_type="image/svg+xml"
        )

    def chart_bt_scores_view(self, request, slug: str):
        experiment = get_object_or_404(Experiment, slug=slug)
        return HttpResponse(
            bradley_terry_svg(experiment), content_type="image/svg+xml"
        )

    @admin.display(description="Live stats")
    def live_stats(self, obj):
        if obj is None or obj.pk is None:
            return "—"
        counts = experiment_counts(obj)
        mean_listen = mean_listen_duration_ms(obj)
        listen_str = (
            f"{mean_listen / 1000:.1f} s" if mean_listen is not None else "—"
        )
        survey_url = reverse("survey:consent", kwargs={"slug": obj.slug})
        is_pairwise = obj.mode == Experiment.Mode.PAIRWISE

        if is_pairwise:
            csv_label = "Pairwise CSV"
            csv_url = reverse(
                "admin:experiments_experiment_pairwise_answers_csv",
                kwargs={"slug": obj.slug},
            )
            chart_url = reverse(
                "admin:experiments_experiment_chart_pairwise_wins",
                kwargs={"slug": obj.slug},
            )
            chart_alt = "Per-model win rates"
        else:
            csv_label = "Answers CSV"
            csv_url = reverse(
                "admin:experiments_experiment_answers_csv",
                kwargs={"slug": obj.slug},
            )
            chart_url = reverse(
                "admin:experiments_experiment_chart_mean_ratings",
                kwargs={"slug": obj.slug},
            )
            chart_alt = "Per-stimulus mean ratings"

        return format_html(
            '<dl style="display:grid;grid-template-columns:max-content 1fr;gap:0.25rem 1rem;">'
            "<dt>Consent page views</dt><dd>{}</dd>"
            "<dt>Started (consented)</dt><dd>{}</dd>"
            "<dt>Completed</dt><dd>{}</dd>"
            "<dt>Dropped out</dt><dd>{}</dd>"
            "<dt>Completion rate</dt><dd>{:.0%}</dd>"
            "<dt>Mean listen duration</dt><dd>{}</dd>"
            "</dl>"
            '<p style="margin-top:1rem;">'
            '<a href="{}">View details</a> · '
            '<a href="{}">{}</a> · '
            '<a href="{}">Demographics CSV</a> · '
            '<a href="{}">Reproducibility JSON</a> · '
            '<a href="{}">Printable</a> · '
            '<a href="{}">Shareable survey link</a>'
            "</p>"
            '<img src="{}" alt="{}" '
            'style="max-width:100%;margin-top:1rem;">',
            counts.consent_page_views,
            counts.total_sessions,
            counts.completed_sessions,
            counts.abandoned_sessions,
            counts.completion_rate,
            listen_str,
            reverse("admin:experiments_experiment_details", kwargs={"slug": obj.slug}),
            csv_url,
            csv_label,
            reverse("admin:experiments_experiment_demographics_csv", kwargs={"slug": obj.slug}),
            reverse("experiments:repro_json", kwargs={"slug": obj.slug}),
            reverse("experiments:printable", kwargs={"slug": obj.slug}),
            survey_url,
            chart_url,
            chart_alt,
        )

    @admin.display(description="Shortcuts")
    def shortcuts(self, obj):
        return format_html(
            '<a href="{}">Details</a> · '
            '<a href="{}">JSON</a> · '
            '<a href="{}">Printable</a> · '
            '<a href="{}">Survey link</a>',
            reverse("admin:experiments_experiment_details", kwargs={"slug": obj.slug}),
            reverse("experiments:repro_json", kwargs={"slug": obj.slug}),
            reverse("experiments:printable", kwargs={"slug": obj.slug}),
            reverse("survey:consent", kwargs={"slug": obj.slug}),
        )


@admin.register(Condition)
class ConditionAdmin(UnfoldModelAdmin):
    list_display = ("name", "experiment")
    list_filter = ("experiment",)
    search_fields = ("name",)


@admin.register(Stimulus)
class StimulusAdmin(UnfoldModelAdmin):
    list_display = ("title", "condition", "kind", "prompt_group", "is_active", "duration_seconds", "sort_order")
    list_filter = ("condition__experiment", "condition", "kind", "is_active")
    search_fields = ("title", "description", "prompt_group")
    readonly_fields = ("duration_seconds", "sha256")

    fieldsets = (
        (
            None,
            {
                "fields": (
                    "condition",
                    "title",
                    "description",
                    "kind",
                    "prompt_group",
                    "is_active",
                    "sort_order",
                ),
            },
        ),
        (
            "Audio (kind = Audio clip)",
            {
                "description": "Upload an mp3/wav/ogg file for audio stimuli.",
                "fields": ("audio", "duration_seconds", "sha256"),
            },
        ),
        (
            "Image (kind = Image)",
            {
                "description": "Upload a png/jpg/webp/gif file for image stimuli.",
                "fields": ("image",),
            },
        ),
        (
            "Text (kind = Text only)",
            {
                "description": "Used for text-only stimuli — rendered with line breaks preserved.",
                "fields": ("text_body",),
            },
        ),
    )


@admin.register(Question)
class QuestionAdmin(UnfoldModelAdmin):
    form = QuestionAdminForm
    list_display = (
        "prompt",
        "experiment",
        "section",
        "type",
        "required",
        "page_break_before",
        "show_prompt",
        "sort_order",
    )
    list_filter = ("experiment", "section", "type")
    search_fields = ("prompt",)
    fieldsets = (
        (
            None,
            {
                "fields": (
                    "experiment",
                    "section",
                    "type",
                    "prompt",
                    "help_text",
                    "required",
                    "page_break_before",
                    "show_prompt",
                    "sort_order",
                ),
            },
        ),
        (
            "Rating slider settings",
            {
                "description": "Used when Type = Rating slider.",
                "fields": (
                    "rating_min",
                    "rating_max",
                    "rating_step",
                    "rating_min_label",
                    "rating_max_label",
                ),
            },
        ),
        (
            "Multiple choice settings",
            {
                "description": "Used when Type = Multiple choice.",
                "fields": ("choice_options", "choice_multi"),
            },
        ),
        (
            "Free text settings",
            {
                "description": "Used when Type = Free text.",
                "fields": ("text_max_length",),
            },
        ),
        (
            "Likert scale settings",
            {
                "description": "Used when Type = Likert scale.",
                "fields": ("likert_steps", "likert_labels"),
            },
        ),
    )
