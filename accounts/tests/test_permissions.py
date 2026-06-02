"""Tests for the central permission helper and the gradual-rollout fallback."""
from __future__ import annotations

import pytest
from django.contrib.auth.models import AnonymousUser

from accounts.models import Membership
from accounts.permissions import (
    can_edit,
    can_manage,
    can_view,
    role_for,
    visible_experiment_ids,
)
from accounts.roles import Role
from accounts.tests.factories import (
    StaffUserFactory,
    SuperUserFactory,
    UserFactory,
)
from experiments.tests.factories import ExperimentFactory

pytestmark = pytest.mark.django_db


def test_owner_has_full_access():
    owner = UserFactory()
    exp = ExperimentFactory(owner=owner)
    assert role_for(owner, exp) == Role.OWNER
    assert can_view(owner, exp)
    assert can_edit(owner, exp)
    assert can_manage(owner, exp)


def test_editor_can_edit_but_not_manage():
    user = UserFactory()
    exp = ExperimentFactory(owner=UserFactory())
    Membership.objects.create(user=user, experiment=exp, role=Role.EDITOR)
    assert can_view(user, exp)
    assert can_edit(user, exp)
    assert not can_manage(user, exp)


def test_viewer_can_only_view():
    user = UserFactory()
    exp = ExperimentFactory(owner=UserFactory())
    Membership.objects.create(user=user, experiment=exp, role=Role.VIEWER)
    assert can_view(user, exp)
    assert not can_edit(user, exp)
    assert not can_manage(user, exp)


def test_outsider_denied_even_if_staff_on_owned_experiment():
    outsider = StaffUserFactory()
    exp = ExperimentFactory(owner=UserFactory())
    assert role_for(outsider, exp) is None
    assert not can_view(outsider, exp)


def test_superuser_bypass():
    su = SuperUserFactory()
    exp = ExperimentFactory(owner=UserFactory())
    assert can_manage(su, exp)


def test_legacy_unowned_experiment_is_staff_accessible_only():
    staff = StaffUserFactory()
    nonstaff = UserFactory()
    exp = ExperimentFactory(owner=None)
    assert can_manage(staff, exp)
    assert role_for(nonstaff, exp) is None


def test_anonymous_denied():
    exp = ExperimentFactory(owner=UserFactory())
    assert role_for(AnonymousUser(), exp) is None


def test_visible_ids_scopes_to_owned_and_member():
    a = UserFactory()
    b = UserFactory()
    exp_a = ExperimentFactory(owner=a)
    exp_b = ExperimentFactory(owner=b)
    shared = ExperimentFactory(owner=b)
    Membership.objects.create(user=a, experiment=shared, role=Role.VIEWER)

    ids = visible_experiment_ids(a)
    assert exp_a.id in ids
    assert shared.id in ids
    assert exp_b.id not in ids


def test_visible_ids_includes_unowned_for_staff_only():
    staff = StaffUserFactory()
    nonstaff = UserFactory()
    legacy = ExperimentFactory(owner=None)
    assert legacy.id in visible_experiment_ids(staff)
    assert legacy.id not in visible_experiment_ids(nonstaff)
