"""Tests for access-control services + their audit-log side effects."""
from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from accounts import services
from accounts.models import AccessEvent, Invitation, Membership
from accounts.roles import Role
from accounts.tests.factories import UserFactory
from experiments.tests.factories import ExperimentFactory

pytestmark = pytest.mark.django_db


def test_invite_creates_pending_and_logs():
    owner = UserFactory()
    exp = ExperimentFactory(owner=owner)
    inv = services.invite_member(exp, "x@e.org", Role.EDITOR, actor=owner)
    assert inv.is_pending()
    assert inv.token
    assert AccessEvent.objects.filter(
        experiment=exp, event_type=AccessEvent.Event.INVITE_SENT
    ).exists()


def test_reinvite_replaces_prior_pending_invite():
    owner = UserFactory()
    exp = ExperimentFactory(owner=owner)
    services.invite_member(exp, "x@e.org", Role.EDITOR, actor=owner)
    services.invite_member(exp, "x@e.org", Role.VIEWER, actor=owner)
    pending = Invitation.objects.filter(
        experiment=exp, email="x@e.org", accepted_at__isnull=True
    )
    assert pending.count() == 1
    assert pending.first().role == Role.VIEWER


def test_accept_creates_membership_and_logs():
    owner = UserFactory()
    invitee = UserFactory()
    exp = ExperimentFactory(owner=owner)
    inv = services.invite_member(exp, invitee.email, Role.EDITOR, actor=owner)
    services.accept_invitation(inv, invitee)
    inv.refresh_from_db()
    assert inv.accepted_at is not None
    assert Membership.objects.filter(
        user=invitee, experiment=exp, role=Role.EDITOR
    ).exists()
    assert AccessEvent.objects.filter(
        experiment=exp, event_type=AccessEvent.Event.INVITE_ACCEPTED
    ).exists()


def test_change_role_and_remove():
    owner = UserFactory()
    member = UserFactory()
    exp = ExperimentFactory(owner=owner)
    m = Membership.objects.create(user=member, experiment=exp, role=Role.VIEWER)
    services.change_role(m, Role.EDITOR, actor=owner)
    m.refresh_from_db()
    assert m.role == Role.EDITOR
    services.remove_member(m, actor=owner)
    assert not Membership.objects.filter(experiment=exp, user=member).exists()
    assert AccessEvent.objects.filter(
        event_type=AccessEvent.Event.ROLE_CHANGED
    ).exists()
    assert AccessEvent.objects.filter(
        event_type=AccessEvent.Event.MEMBER_REMOVED
    ).exists()


def test_transfer_ownership_demotes_previous_owner():
    owner = UserFactory()
    successor = UserFactory()
    exp = ExperimentFactory(owner=owner)
    services.grant_owner_membership(exp, owner)
    Membership.objects.create(user=successor, experiment=exp, role=Role.EDITOR)
    services.transfer_ownership(exp, successor, actor=owner)
    exp.refresh_from_db()
    assert exp.owner == successor
    assert (
        Membership.objects.get(user=successor, experiment=exp).role == Role.OWNER
    )
    assert (
        Membership.objects.get(user=owner, experiment=exp).role == Role.EDITOR
    )
    assert AccessEvent.objects.filter(
        event_type=AccessEvent.Event.OWNERSHIP_TRANSFERRED
    ).exists()


def test_expired_invitation_is_not_pending():
    owner = UserFactory()
    exp = ExperimentFactory(owner=owner)
    inv = services.invite_member(exp, "x@e.org", Role.EDITOR, actor=owner)
    inv.expires_at = timezone.now() - timedelta(days=1)
    inv.save(update_fields=["expires_at"])
    assert not inv.is_pending()
    assert inv.status == "expired"
