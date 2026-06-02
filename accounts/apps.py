from django.apps import AppConfig


class AccountsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "accounts"
    verbose_name = "Accounts & access"

    def ready(self) -> None:
        # Registers the post_save handler that backfills a Profile per User.
        from . import signals  # noqa: F401
