"""Auto-create a :class:`accounts.models.Profile` for every new ``User``.

Imported from ``AccountsConfig.ready()``; the app registry is loaded by then so
``get_user_model()`` resolves correctly. Data migrations use historical models
and do not fire signals, so the initial backfill is handled explicitly in
``accounts/migrations/0002_backfill_profiles.py``.
"""
from __future__ import annotations

from django.contrib.auth import get_user_model
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import Profile

User = get_user_model()


@receiver(post_save, sender=User, dispatch_uid="accounts.ensure_profile")
def ensure_profile(sender, instance, created, **kwargs):
    if created:
        Profile.objects.get_or_create(user=instance)
