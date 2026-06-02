"""Assign an owner (+ matching OWNER membership) to pre-existing experiments.

Existing studies predate the ownership model, so they have ``owner=NULL``. We
assign them to the first active superuser and create the canonical OWNER
membership row so the "owner is also a Membership" invariant holds from day one.

If there is no superuser, owners are left NULL: ``permissions.role_for`` grants
superusers access regardless, and an admin can assign owners afterwards from the
Django admin or the studio dashboard.
"""
from django.conf import settings
from django.db import migrations


def backfill_owner(apps, schema_editor):
    User = apps.get_model(settings.AUTH_USER_MODEL)
    Experiment = apps.get_model("experiments", "Experiment")
    Membership = apps.get_model("accounts", "Membership")
    db = schema_editor.connection.alias

    superuser = (
        User.objects.using(db)
        .filter(is_superuser=True, is_active=True)
        .order_by("pk")
        .first()
    )
    if superuser is None:
        return

    for exp in Experiment.objects.using(db).filter(owner__isnull=True):
        exp.owner_id = superuser.pk
        exp.save(update_fields=["owner"])
        Membership.objects.using(db).get_or_create(
            user_id=superuser.pk,
            experiment_id=exp.pk,
            defaults={"role": "owner"},
        )


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("experiments", "0010_experiment_owner"),
        ("accounts", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [migrations.RunPython(backfill_owner, noop)]
