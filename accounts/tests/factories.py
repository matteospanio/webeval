"""Factory-boy factories for accounts + access models."""
from __future__ import annotations

import factory
from django.contrib.auth.models import User
from factory.django import DjangoModelFactory

from accounts.models import Invitation, Membership
from accounts.roles import Role
from experiments.tests.factories import ExperimentFactory


class UserFactory(DjangoModelFactory):
    class Meta:
        model = User
        django_get_or_create = ("username",)

    username = factory.Sequence(lambda n: f"user{n}")
    email = factory.LazyAttribute(lambda o: f"{o.username}@example.org")
    is_staff = False
    is_active = True


class StaffUserFactory(UserFactory):
    is_staff = True


class SuperUserFactory(UserFactory):
    is_staff = True
    is_superuser = True


class MembershipFactory(DjangoModelFactory):
    class Meta:
        model = Membership

    user = factory.SubFactory(UserFactory)
    experiment = factory.SubFactory(ExperimentFactory)
    role = Role.EDITOR


class InvitationFactory(DjangoModelFactory):
    class Meta:
        model = Invitation

    experiment = factory.SubFactory(ExperimentFactory)
    email = factory.Sequence(lambda n: f"invitee{n}@example.org")
    role = Role.EDITOR
