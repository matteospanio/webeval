"""Create a Profile for every pre-existing User.

The post_save signal only fires for users created after this app is installed;
RunPython uses historical models and does not fire signals, so existing users
need an explicit backfill.
"""
from django.conf import settings
from django.db import migrations


def create_profiles(apps, schema_editor):
    User = apps.get_model(settings.AUTH_USER_MODEL)
    Profile = apps.get_model("accounts", "Profile")
    db = schema_editor.connection.alias
    for user_id in User.objects.using(db).values_list("id", flat=True):
        Profile.objects.using(db).get_or_create(user_id=user_id)


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [migrations.RunPython(create_profiles, noop)]
