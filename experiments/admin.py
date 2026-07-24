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

from django import forms
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Max, Q
from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.urls import path, reverse
from django.utils.html import format_html
from unfold.admin import ModelAdmin as UnfoldModelAdmin
from unfold.admin import TabularInline as UnfoldTabularInline
from unfold.decorators import action

from accounts.admin_mixins import OwnerScopedAdminMixin
from accounts.models import AuditEvent
from accounts.permissions import can_edit, can_manage, can_view
from accounts.services import grant_owner_membership, record_audit

from .assignment import available_pairwise_strategies, available_strategies
from .charts import bradley_terry_svg, mean_ratings_svg, pairwise_win_rates_svg
from .csv_exports import (
    answers_csv_response,
    demographics_csv_response,
    pairwise_answers_csv_response,
)
from .data_ops import activate_from_test, purge_participant_data
from .exports import build_experiment_archive
from .forms import QuestionAdminForm, QuestionTemplateAdminForm
from .imports import import_experiment_archive
from .models import (
    Condition,
    Experiment,
    ParticipantInvite,
    Prompt,
    Question,
    QuestionTemplate,
    Stimulus,
    Webhook,
)
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


class PromptInline(_ReadOnlyWhenLockedMixin, UnfoldTabularInline):
    model = Prompt
    extra = 0
    fields = ("prompt_group", "title", "audio", "description", "duration_seconds")
    readonly_fields = ("duration_seconds",)
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


@admin.action(description="Generate 20 participant invite links")
def generate_participant_invites(modeladmin, request, queryset):
    created = 0
    for exp in queryset:
        ParticipantInvite.objects.bulk_create(
            [ParticipantInvite(experiment=exp) for _ in range(20)]
        )
        created += 20
    modeladmin.message_user(
        request, f"Generated {created} invite links.", level=messages.SUCCESS
    )


@admin.action(description="Duplicate selected studies (new draft you own)")
def duplicate_experiment(modeladmin, request, queryset):
    from experiments.cloning import clone_experiment

    cloned = 0
    for exp in queryset:
        clone = clone_experiment(exp, owner=request.user)
        grant_owner_membership(clone, request.user, actor=request.user)
        cloned += 1
    modeladmin.message_user(
        request,
        f"Duplicated {cloned} stud{'y' if cloned == 1 else 'ies'} into new drafts.",
        level=messages.SUCCESS,
    )


@admin.register(Webhook)
class WebhookAdmin(OwnerScopedAdminMixin, UnfoldModelAdmin):
    experiment_lookup = "experiment"
    list_display = (
        "experiment", "event", "url", "is_active", "last_status", "last_delivered_at"
    )
    list_filter = ("event", "is_active")
    search_fields = ("experiment__name", "url")
    readonly_fields = ("secret", "created_at", "last_delivered_at", "last_status", "last_error")


@admin.register(ParticipantInvite)
class ParticipantInviteAdmin(OwnerScopedAdminMixin, UnfoldModelAdmin):
    experiment_lookup = "experiment"
    list_display = ("token", "experiment", "label", "used_at", "created_at")
    list_filter = ("experiment",)
    search_fields = ("token", "label", "experiment__name")
    readonly_fields = ("token", "used_at", "created_at")


@admin.register(Experiment)
class ExperimentAdmin(OwnerScopedAdminMixin, UnfoldModelAdmin):
    experiment_lookup = "pk"
    list_display = ("name", "slug", "state", "mode", "owner", "assignment_strategy", "created_at", "shortcuts")
    list_filter = ("state", "mode", "assignment_strategy")
    search_fields = ("name", "slug", "description")
    prepopulated_fields = {"slug": ("name",)}
    inlines = (ConditionInline, QuestionInline, PromptInline)
    actions = (
        export_repro_json,
        open_printable,
        generate_participant_invites,
        duplicate_experiment,
    )
    actions_list = ("import_experiment",)
    readonly_fields = ("live_stats", "owner")
    autocomplete_fields = ("follows",)

    fieldsets = (
        (None, {"fields": ("name", "slug", "owner", "state", "mode", "description")}),
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
                    "randomize_stimulus_questions",
                    "eligibility_rule",
                    "min_completion_seconds",
                    "one_submission_per_participant",
                    "completion_code_mode",
                    "completion_code",
                    "external_id_param",
                    "bot_protection",
                    "access_mode",
                    "access_code",
                    "collect_participant_code",
                    "participant_code_label",
                )
            },
        ),
        (
            "Longitudinal (multi-phase)",
            {
                "description": (
                    "Link studies into phases a participant completes over time. "
                    "Set 'follows' to the previous phase; both phases must collect "
                    "a participant code."
                ),
                "fields": ("follows", "phase_gap_hours"),
            },
        ),
        (
            "Branding",
            {
                "description": (
                    "Customise the look of the participant-facing pages for this "
                    "study. All optional."
                ),
                "fields": (
                    "brand_primary_color",
                    "brand_logo",
                    "brand_custom_css",
                ),
            },
        ),
        (
            "Compliance & governance",
            {
                "description": (
                    "Ethics / data-protection metadata. 'Retention days' drives "
                    "the purge_expired_data command (0 = keep indefinitely)."
                ),
                "fields": (
                    "irb_number",
                    "legal_basis",
                    "data_contact",
                    "retention_days",
                ),
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

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        if db_field.name == "assignment_strategy":
            choices = [
                (name, f"{name} (standard)") for name in available_strategies()
            ] + [
                (name, f"{name} (pairwise)") for name in available_pairwise_strategies()
            ]
            return forms.ChoiceField(
                choices=choices,
                initial=db_field.default,
                help_text=db_field.help_text,
                label=db_field.verbose_name.capitalize(),
            )
        return super().formfield_for_dbfield(db_field, request, **kwargs)

    def save_model(self, request, obj, form, change):
        creating = not change
        if creating and obj.owner_id is None:
            obj.owner = request.user
        super().save_model(request, obj, form, change)
        if creating and obj.owner_id is not None:
            grant_owner_membership(obj, obj.owner, actor=request.user)
        record_audit(
            obj, AuditEvent.Action.EDIT, actor=request.user,
            target=obj.slug, request=request, created=creating,
        )

    def _scoped_experiment(self, request, slug, require=can_view):
        """Fetch an experiment for a per-experiment admin view, enforcing
        object-level access. These views are otherwise only login-gated by
        ``admin_view`` — without this check any staff user could read any
        study's results by guessing the slug."""
        experiment = get_object_or_404(Experiment, slug=slug)
        if not require(request.user, experiment):
            raise PermissionDenied
        return experiment

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
            path(
                "<slug:slug>/export.zip",
                self.admin_site.admin_view(self.experiment_export_zip_view),
                name="experiments_experiment_export_zip",
            ),
            path(
                "import/",
                self.admin_site.admin_view(self.experiment_import_view),
                name="experiments_experiment_import",
            ),
            path(
                "<slug:slug>/activate/",
                self.admin_site.admin_view(self.activate_view),
                name="experiments_experiment_activate",
            ),
            path(
                "<slug:slug>/add-from-bank/",
                self.admin_site.admin_view(self.add_from_bank_view),
                name="experiments_experiment_add_from_bank",
            ),
        ]
        # Custom routes must come before the generic ``<path:object_id>/``
        # entry Django registers for change/delete views, otherwise the
        # slug gets swallowed by the object-id matcher.
        return custom + urls

    def experiment_details_view(self, request, slug: str):
        experiment = self._scoped_experiment(request, slug)
        context = {
            **self.admin_site.each_context(request),
            "experiment": experiment,
            "counts": experiment_counts(experiment),
            "mean_listen_ms": mean_listen_duration_ms(experiment),
        }
        if experiment.is_pairwise:
            context["pairwise_stats"] = pairwise_experiment_stats(experiment)
            context["bt_stats"] = bradley_terry_analysis(experiment)
            context["bt_chart_svg"] = bradley_terry_svg(experiment)
        else:
            context["per_stimulus"] = per_stimulus_mean_ratings(experiment)
            context["chart_svg"] = mean_ratings_svg(experiment)
        return render(request, "admin/experiments/experiment/details.html", context)

    def answers_csv_view(self, request, slug: str):
        experiment = self._scoped_experiment(request, slug)
        return answers_csv_response(experiment)

    def demographics_csv_view(self, request, slug: str):
        experiment = self._scoped_experiment(request, slug)
        return demographics_csv_response(experiment)

    def chart_mean_ratings_view(self, request, slug: str):
        experiment = self._scoped_experiment(request, slug)
        return HttpResponse(
            mean_ratings_svg(experiment), content_type="image/svg+xml"
        )

    def pairwise_answers_csv_view(self, request, slug: str):
        experiment = self._scoped_experiment(request, slug)
        return pairwise_answers_csv_response(experiment)

    def chart_pairwise_wins_view(self, request, slug: str):
        experiment = self._scoped_experiment(request, slug)
        return HttpResponse(
            pairwise_win_rates_svg(experiment), content_type="image/svg+xml"
        )

    def chart_bt_scores_view(self, request, slug: str):
        experiment = self._scoped_experiment(request, slug)
        return HttpResponse(
            bradley_terry_svg(experiment), content_type="image/svg+xml"
        )

    def experiment_export_zip_view(self, request, slug: str):
        experiment = self._scoped_experiment(request, slug)
        payload = build_experiment_archive(experiment)
        response = HttpResponse(payload, content_type="application/zip")
        response["Content-Disposition"] = (
            f'attachment; filename="{experiment.slug}.zip"'
        )
        response["Content-Length"] = str(len(payload))
        return response

    def experiment_import_view(self, request):
        context = {
            **self.admin_site.each_context(request),
            "title": "Import experiment",
            "form_error": None,
            "slug_override": "",
        }
        if request.method == "POST":
            uploaded = request.FILES.get("archive")
            slug_override = (request.POST.get("slug_override") or "").strip() or None
            context["slug_override"] = slug_override or ""
            if uploaded is None:
                context["form_error"] = "Select a ZIP archive to upload."
            else:
                try:
                    experiment = import_experiment_archive(
                        uploaded, slug_override=slug_override
                    )
                except ValidationError as exc:
                    context["form_error"] = "; ".join(exc.messages)
                else:
                    experiment.owner = request.user
                    experiment.save(update_fields=["owner"])
                    grant_owner_membership(
                        experiment, request.user, actor=request.user
                    )
                    self.message_user(
                        request,
                        f"Imported experiment '{experiment.name}' as draft.",
                        level=messages.SUCCESS,
                    )
                    return HttpResponseRedirect(
                        reverse(
                            "admin:experiments_experiment_change",
                            args=[experiment.pk],
                        )
                    )
        return render(
            request,
            "admin/experiments/experiment/import.html",
            context,
        )

    @action(
        description="Import experiment",
        url_path="import-action",
        icon="upload",
    )
    def import_experiment(self, request):
        return HttpResponseRedirect(
            reverse("admin:experiments_experiment_import")
        )

    def activate_view(self, request, slug: str):
        experiment = self._scoped_experiment(request, slug, require=can_manage)

        change_url = reverse(
            "admin:experiments_experiment_change", args=[experiment.pk]
        )

        if experiment.state != Experiment.State.TEST:
            self.message_user(
                request,
                (
                    f"Experiment '{experiment.name}' is "
                    f"{experiment.get_state_display().lower()}; the Activate "
                    "confirmation page is only for test-phase experiments."
                ),
                level=messages.WARNING,
            )
            return HttpResponseRedirect(change_url)

        counts = experiment_counts(experiment)
        from experiments.readiness import readiness_problems

        problems = readiness_problems(experiment)

        if request.method == "POST":
            if problems:
                self.message_user(
                    request,
                    "Cannot activate yet — " + " ".join(problems),
                    level=messages.ERROR,
                )
                return HttpResponseRedirect(request.path)
            # Shared with the studio lifecycle view so the purge-or-promote
            # data handling can never drift between the two entry points.
            purged_counts = activate_from_test(
                experiment, purge=request.POST.get("purge") == "on"
            )
            if purged_counts is not None:
                record_audit(
                    experiment, AuditEvent.Action.PURGE, actor=request.user,
                    target="test-phase data", request=request,
                    sessions=purged_counts.sessions,
                )
            record_audit(
                experiment, AuditEvent.Action.ACTIVATE, actor=request.user,
                target=experiment.slug, request=request,
            )
            if purged_counts is not None:
                self.message_user(
                    request,
                    (
                        f"Activated '{experiment.name}' and purged "
                        f"{purged_counts.sessions} sessions, "
                        f"{purged_counts.responses} responses collected "
                        "during testing."
                    ),
                    level=messages.SUCCESS,
                )
            else:
                self.message_user(
                    request,
                    (
                        f"Activated '{experiment.name}' — test-phase data "
                        "was kept."
                    ),
                    level=messages.SUCCESS,
                )
            return HttpResponseRedirect(change_url)

        context = {
            **self.admin_site.each_context(request),
            "experiment": experiment,
            "counts": counts,
            "change_url": change_url,
            "readiness_problems": problems,
        }
        return render(
            request,
            "admin/experiments/experiment/activate.html",
            context,
        )

    def add_from_bank_view(self, request, slug: str):
        experiment = self._scoped_experiment(request, slug, require=can_edit)
        change_url = reverse(
            "admin:experiments_experiment_change", args=[experiment.pk]
        )
        if experiment.state != Experiment.State.DRAFT:
            self.message_user(
                request,
                "Questions can only be added from the bank while the study is a "
                "draft.",
                level=messages.WARNING,
            )
            return HttpResponseRedirect(change_url)

        templates = QuestionTemplate.objects.filter(
            Q(owner=request.user) | Q(owner__isnull=True)
        ).order_by("name")

        if request.method == "POST":
            chosen = list(templates.filter(pk__in=request.POST.getlist("template")))
            start = (
                experiment.questions.aggregate(m=Max("sort_order"))["m"] or 0
            ) + 1
            for offset, tpl in enumerate(chosen):
                tpl.build_question(experiment, sort_order=start + offset).save()
            self.message_user(
                request,
                f"Added {len(chosen)} question(s) from your bank.",
                level=messages.SUCCESS,
            )
            return HttpResponseRedirect(change_url)

        context = {
            **self.admin_site.each_context(request),
            "experiment": experiment,
            "templates": templates,
            "change_url": change_url,
            "title": f"Add questions from bank — {experiment.name}",
        }
        return render(
            request,
            "admin/experiments/experiment/add_from_bank.html",
            context,
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
        is_pairwise = obj.is_pairwise

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

        activate_banner = ""
        if obj.state == Experiment.State.TEST:
            activate_url = reverse(
                "admin:experiments_experiment_activate", kwargs={"slug": obj.slug}
            )
            activate_banner = format_html(
                '<p style="margin-top:1rem;padding:0.75rem 1rem;'
                'border:1px solid var(--base-200,#e5e7eb);border-radius:6px;">'
                "This experiment is in <strong>Test</strong> mode. "
                'Responses collected here are testing data — '
                '<a href="{}"><strong>Activate</strong></a> to promote it to '
                "live data collection.</p>",
                activate_url,
            )

        base_html = format_html(
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
            '<a href="{}">Reproducibility ZIP</a> · '
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
            reverse("admin:experiments_experiment_export_zip", kwargs={"slug": obj.slug}),
            reverse("experiments:printable", kwargs={"slug": obj.slug}),
            survey_url,
            chart_url,
            chart_alt,
        )
        if activate_banner:
            return format_html("{}{}", activate_banner, base_html)
        return base_html

    @admin.display(description="Shortcuts")
    def shortcuts(self, obj):
        base = format_html(
            '<a href="{}">Details</a> · '
            '<a href="{}">JSON</a> · '
            '<a href="{}">ZIP</a> · '
            '<a href="{}">Printable</a> · '
            '<a href="{}">Survey link</a>',
            reverse("admin:experiments_experiment_details", kwargs={"slug": obj.slug}),
            reverse("experiments:repro_json", kwargs={"slug": obj.slug}),
            reverse("admin:experiments_experiment_export_zip", kwargs={"slug": obj.slug}),
            reverse("experiments:printable", kwargs={"slug": obj.slug}),
            reverse("survey:consent", kwargs={"slug": obj.slug}),
        )
        if obj.state == Experiment.State.TEST:
            return format_html(
                '{} · <a href="{}"><strong>Activate</strong></a>',
                base,
                reverse(
                    "admin:experiments_experiment_activate",
                    kwargs={"slug": obj.slug},
                ),
            )
        return base


@admin.register(Condition)
class ConditionAdmin(OwnerScopedAdminMixin, UnfoldModelAdmin):
    experiment_lookup = "experiment"
    list_display = ("name", "experiment")
    list_filter = ("experiment",)
    search_fields = ("name",)


@admin.register(Stimulus)
class StimulusAdmin(OwnerScopedAdminMixin, UnfoldModelAdmin):
    experiment_lookup = "condition__experiment"
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
                "fields": ("audio",),
            },
        ),
        (
            "Video (kind = Video)",
            {
                "description": "Upload an mp4/webm/mov file for video stimuli.",
                "fields": ("video",),
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
            "Text / HTML (kind = Text only or HTML snippet)",
            {
                "description": "Plain text is rendered with line breaks; for the HTML kind the same field is rendered as raw HTML to participants.",
                "fields": ("text_body",),
            },
        ),
        (
            "Embedded URL (kind = Embedded URL)",
            {
                "description": "External URL shown to participants in an iframe (e.g. a hosted player or widget).",
                "fields": ("embed_url",),
            },
        ),
        (
            "Computed metadata",
            {"fields": ("duration_seconds", "sha256")},
        ),
    )


@admin.register(Prompt)
class PromptAdmin(OwnerScopedAdminMixin, UnfoldModelAdmin):
    experiment_lookup = "experiment"
    list_display = ("title", "experiment", "prompt_group", "duration_seconds")
    list_filter = ("experiment",)
    search_fields = ("prompt_group", "title", "description")
    readonly_fields = ("duration_seconds", "sha256", "created_at", "updated_at")
    autocomplete_fields = ("experiment",)
    fields = (
        "experiment",
        "prompt_group",
        "title",
        "description",
        "audio",
        "duration_seconds",
        "sha256",
        "created_at",
        "updated_at",
    )


@admin.action(description="Save selected questions to my question bank")
def save_questions_to_bank(modeladmin, request, queryset):
    created = [
        QuestionTemplate.from_question(q, owner=request.user) for q in queryset
    ]
    QuestionTemplate.objects.bulk_create(created)
    modeladmin.message_user(
        request,
        f"Saved {len(created)} question(s) to your bank.",
        level=messages.SUCCESS,
    )


@admin.register(Question)
class QuestionAdmin(OwnerScopedAdminMixin, UnfoldModelAdmin):
    experiment_lookup = "experiment"
    actions = (save_questions_to_bank,)
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
            "Display logic (skip / branching)",
            {
                "description": (
                    "Optional. Show this question only when earlier answers in "
                    'the same section match. JSON, e.g. {"question": 12, "op": '
                    '"eq", "value": "Yes"}. The referenced question must have a '
                    "lower sort order."
                ),
                "fields": ("visible_if",),
            },
        ),
        (
            "Attention check & PII",
            {
                "description": (
                    "Optional. If set, this question is an attention check; a "
                    "participant whose answer differs from the expected value "
                    'is flagged. Enter the expected answer as JSON, e.g. '
                    '"Strongly agree" or 4. Tick "contains PII" to redact this '
                    "question's free-text answers from exports by default."
                ),
                "fields": ("attention_expected", "contains_pii"),
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
        (
            "Numeric input settings",
            {
                "description": "Used when Type = Numeric input. All fields are optional.",
                "fields": (
                    "numeric_min",
                    "numeric_max",
                    "numeric_integer",
                    "numeric_unit",
                ),
            },
        ),
        (
            "Matrix (grid) settings",
            {
                "description": "Used when Type = Matrix (grid): rows are the sub-questions, columns the shared answer scale.",
                "fields": ("matrix_rows", "matrix_columns"),
            },
        ),
        (
            "Ranking / ordering settings",
            {
                "description": "Used when Type = Ranking / ordering.",
                "fields": ("ranking_items",),
            },
        ),
        (
            "Custom (plugin) question config",
            {
                "description": (
                    "Used only when Type is a custom plugin component "
                    "(see experiments.components). Enter the component's raw "
                    "JSON config; built-in types ignore this field."
                ),
                "fields": ("plugin_config",),
                "classes": ("collapse",),
            },
        ),
    )


@admin.register(QuestionTemplate)
class QuestionTemplateAdmin(UnfoldModelAdmin):
    """The reusable question bank. Each user sees their own templates plus any
    shared (owner-less) ones; superusers see everything."""

    form = QuestionTemplateAdminForm
    list_display = ("name", "owner", "section", "type", "required", "created_at")
    list_filter = ("section", "type")
    search_fields = ("name", "prompt")
    readonly_fields = ("created_at", "updated_at")

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        return qs.filter(Q(owner=request.user) | Q(owner__isnull=True))

    def save_model(self, request, obj, form, change):
        # New templates default to being owned by their creator.
        if not change and obj.owner_id is None:
            obj.owner = request.user
        super().save_model(request, obj, form, change)
