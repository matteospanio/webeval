"""Django settings for the webeval project.

Settings are driven by environment variables (see ``.env.example``). The project
is a research web app for collecting anonymous human evaluations of LLM-generated
symbolic music; see ``README.md`` for the product requirements.
"""

from pathlib import Path

import environ
from django.urls import reverse_lazy

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env(
    DEBUG=(bool, False),
    ALLOWED_HOSTS=(list, ["localhost", "127.0.0.1"]),
)

# Read .env if present. Missing .env is fine — envvars may be set directly.
environ.Env.read_env(BASE_DIR / ".env")

SECRET_KEY = env(
    "SECRET_KEY",
    default="django-insecure-dev-only-do-not-use-in-production",
)
DEBUG = env("DEBUG")
ALLOWED_HOSTS = env("ALLOWED_HOSTS")

INSTALLED_APPS = [
    # django-unfold must load before django.contrib.admin so its template
    # overrides (admin/base_site.html, etc.) win the template search.
    "unfold",
    "unfold.contrib.forms",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "experiments",
    "survey",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "core.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "core.context_processors.admin_summary",
            ],
        },
    },
]

WSGI_APPLICATION = "core.wsgi.application"

DATABASES = {
    "default": env.db_url(
        "DATABASE_URL",
        default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}",
    ),
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

MEDIA_URL = "media/"
MEDIA_ROOT = env("MEDIA_ROOT", default=str(BASE_DIR / "media"))

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- webeval-specific ----------------------------------------------------

# Path to an offline MaxMind GeoLite2-Country.mmdb file. If unset or missing,
# country-code capture degrades gracefully to None rather than erroring.
GEOIP_PATH = env("GEOIP_PATH", default=None)

# Max upload size for audio stimuli, in bytes (~10 MB by default).
STIMULUS_MAX_UPLOAD_BYTES = env.int("STIMULUS_MAX_UPLOAD_BYTES", default=10 * 1024 * 1024)

# Allowed audio extensions + MIME types for stimulus uploads.
STIMULUS_ALLOWED_EXTENSIONS = ("mp3", "wav", "ogg")
STIMULUS_ALLOWED_MIME_TYPES = (
    "audio/mpeg",
    "audio/mp3",
    "audio/wav",
    "audio/x-wav",
    "audio/wave",
    "audio/ogg",
    "audio/vorbis",
)

# Image stimulus upload limits (used when Stimulus.kind == "image").
STIMULUS_MAX_IMAGE_UPLOAD_BYTES = env.int(
    "STIMULUS_MAX_IMAGE_UPLOAD_BYTES", default=5 * 1024 * 1024
)
STIMULUS_ALLOWED_IMAGE_EXTENSIONS = ("png", "jpg", "jpeg", "webp", "gif")

# --- django-unfold theme ------------------------------------------------
#
# Unfold re-skins the Django admin. ModelAdmins must inherit from
# ``unfold.admin.ModelAdmin`` / ``unfold.admin.TabularInline`` for the
# theme to apply consistently; see experiments/admin.py.
UNFOLD = {
    "SITE_TITLE": "webeval admin",
    "SITE_HEADER": "webeval",
    "SITE_SUBHEADER": "Human evaluation of LLM-generated stimuli",
    "SHOW_HISTORY": True,
    "SIDEBAR": {
        "show_search": True,
        # Single curated sidebar — disable the auto app list so there's no
        # duplicate navigation column.
        "show_all_applications": False,
        "navigation": [
            {
                "title": "Overview",
                "separator": True,
                "items": [
                    {
                        "title": "Summary",
                        "icon": "dashboard",
                        "link": reverse_lazy("admin:index"),
                    },
                ],
            },
            {
                "title": "Studies",
                "separator": True,
                "items": [
                    {
                        "title": "Experiments",
                        "icon": "science",
                        "link": reverse_lazy(
                            "admin:experiments_experiment_changelist"
                        ),
                    },
                    {
                        "title": "Conditions",
                        "icon": "category",
                        "link": reverse_lazy(
                            "admin:experiments_condition_changelist"
                        ),
                    },
                    {
                        "title": "Stimuli",
                        "icon": "library_music",
                        "link": reverse_lazy(
                            "admin:experiments_stimulus_changelist"
                        ),
                    },
                    {
                        "title": "Questions",
                        "icon": "quiz",
                        "link": reverse_lazy(
                            "admin:experiments_question_changelist"
                        ),
                    },
                ],
            },
            {
                "title": "Participants",
                "separator": True,
                "items": [
                    {
                        "title": "Sessions",
                        "icon": "groups",
                        "link": reverse_lazy(
                            "admin:survey_participantsession_changelist"
                        ),
                    },
                    {
                        "title": "Responses",
                        "icon": "fact_check",
                        "link": reverse_lazy(
                            "admin:survey_response_changelist"
                        ),
                    },
                ],
            },
        ],
    },
}
