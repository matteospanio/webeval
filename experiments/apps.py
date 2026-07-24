from django.apps import AppConfig


class ExperimentsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "experiments"
    verbose_name = "Experiments"

    def ready(self):
        from django.utils.module_loading import autodiscover_modules

        # Import the registry module (registers the shipped example component),
        # then let any installed app contribute its own components by defining
        # a ``question_components`` module (legacy hook, supported forever).
        from . import components  # noqa: F401
        autodiscover_modules("question_components")
        # The unified plugin surface (@plugin): any installed app may define a
        # ``panel_plugins`` module registering plugins of every kind.
        from . import checks, plugins  # noqa: F401
        autodiscover_modules("panel_plugins")
