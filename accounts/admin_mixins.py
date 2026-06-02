"""Owner-scoping mixin for ModelAdmins over experiment-derived data.

Restricts non-superuser staff to experiments they own or collaborate on.
Each ModelAdmin sets ``experiment_lookup`` to the ORM path from its model to
``experiments.Experiment``:

* ``"pk"`` — the model *is* ``Experiment``,
* ``"experiment"`` — a direct FK (Condition, Question, Prompt),
* ``"condition__experiment"`` — Stimulus,
* ``"session__experiment"`` — survey sessions, and so on.

Superusers are never restricted.
"""
from __future__ import annotations

from .permissions import can_edit, can_manage, can_view, visible_experiment_ids


class OwnerScopedAdminMixin:
    experiment_lookup: str = "experiment"

    def _experiment_of(self, obj):
        if self.experiment_lookup == "pk":
            return obj
        value = obj
        for part in self.experiment_lookup.split("__"):
            value = getattr(value, part)
        return value

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        ids = visible_experiment_ids(request.user)
        if self.experiment_lookup == "pk":
            return qs.filter(pk__in=ids)
        return qs.filter(**{f"{self.experiment_lookup}__in": ids})

    def has_view_permission(self, request, obj=None):
        if not super().has_view_permission(request, obj):
            return False
        if obj is None or request.user.is_superuser:
            return True
        return can_view(request.user, self._experiment_of(obj))

    def has_change_permission(self, request, obj=None):
        if not super().has_change_permission(request, obj):
            return False
        if obj is None or request.user.is_superuser:
            return True
        return can_edit(request.user, self._experiment_of(obj))

    def has_delete_permission(self, request, obj=None):
        if not super().has_delete_permission(request, obj):
            return False
        if obj is None or request.user.is_superuser:
            return True
        return can_manage(request.user, self._experiment_of(obj))
