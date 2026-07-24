"""Django settings for the PANEL project.

Settings are driven by environment variables (see ``.env.example``). The project
is a research web app for collecting anonymous human evaluations of LLM-generated
symbolic music; see ``README.md`` for the product requirements.
"""

from pathlib import Path

import environ
from django.templatetags.static import static
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
    "rest_framework",
    "apikeys",
    "accounts",
    "experiments",
    "survey",
    "studio",
]

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "apikeys.auth.APIKeyAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAdminUser",
    ],
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
    ],
    "DEFAULT_PARSER_CLASSES": [
        "rest_framework.parsers.MultiPartParser",
        "rest_framework.parsers.FormParser",
        "rest_framework.parsers.JSONParser",
    ],
    # Rate limiting (uses the cache below; configure REDIS_URL in production so
    # limits are shared across worker processes).
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "anon": env("API_THROTTLE_ANON", default="30/min"),
        "user": env("API_THROTTLE_USER", default="240/min"),
    },
}

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

# --- Authentication / accounts ------------------------------------------
#
# Researchers sign in to the studio dashboard (not the Django admin), so the
# auth machinery points at the accounts app rather than admin login.
LOGIN_URL = reverse_lazy("accounts:login")
LOGIN_REDIRECT_URL = reverse_lazy("studio:studies")
LOGOUT_REDIRECT_URL = reverse_lazy("accounts:login")

# Allow self-service researcher registration. Set False to make the platform
# invitation-only (admins create users; collaborators join via invite links).
ACCOUNTS_ALLOW_REGISTRATION = env.bool("ACCOUNTS_ALLOW_REGISTRATION", default=True)

# Email — defaults to the console backend so invitation links are printed to
# the runserver log in development. Configure SMTP via EMAIL_* in production.
EMAIL_BACKEND = env(
    "EMAIL_BACKEND",
    default="django.core.mail.backends.console.EmailBackend",
)
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default="PANEL <no-reply@localhost>")

# Absolute base URL for links built outside a request (reserved for future
# notification hooks; invite links in-app are built from the request host).
SITE_URL = env("SITE_URL", default="http://127.0.0.1:8000")

# --- PANEL-specific ----------------------------------------------------

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

# Video stimulus upload limits (used when Stimulus.kind == "video").
STIMULUS_MAX_VIDEO_UPLOAD_BYTES = env.int(
    "STIMULUS_MAX_VIDEO_UPLOAD_BYTES", default=50 * 1024 * 1024
)
STIMULUS_ALLOWED_VIDEO_EXTENSIONS = ("mp4", "webm", "ogv", "mov", "m4v")

# --- django-unfold theme ------------------------------------------------
#
# Unfold re-skins the Django admin. ModelAdmins must inherit from
# ``unfold.admin.ModelAdmin`` / ``unfold.admin.TabularInline`` for the
# theme to apply consistently; see experiments/admin.py.
UNFOLD = {
    "SITE_TITLE": "PANEL admin",
    "SITE_HEADER": "PANEL",
    "SITE_SUBHEADER": "Human evaluation of LLM-generated stimuli",
    "SITE_ICON": lambda request: static("img/logo.png"),
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
                    {
                        "title": "Studio dashboard",
                        "icon": "rocket_launch",
                        "link": reverse_lazy("studio:studies"),
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
                    {
                        "title": "Question bank",
                        "icon": "library_books",
                        "link": reverse_lazy(
                            "admin:experiments_questiontemplate_changelist"
                        ),
                    },
                    {
                        "title": "Prompts",
                        "icon": "graphic_eq",
                        "link": reverse_lazy(
                            "admin:experiments_prompt_changelist"
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
                    {
                        "title": "Event log",
                        "icon": "timeline",
                        "link": reverse_lazy(
                            "admin:survey_surveyevent_changelist"
                        ),
                    },
                ],
            },
            {
                "title": "Users & access",
                "separator": True,
                "items": [
                    {
                        "title": "Users",
                        "icon": "person",
                        "link": reverse_lazy("admin:auth_user_changelist"),
                    },
                    {
                        "title": "Groups",
                        "icon": "groups",
                        "link": reverse_lazy("admin:auth_group_changelist"),
                    },
                    {
                        "title": "Memberships",
                        "icon": "badge",
                        "link": reverse_lazy(
                            "admin:accounts_membership_changelist"
                        ),
                    },
                    {
                        "title": "Invitations",
                        "icon": "mail",
                        "link": reverse_lazy(
                            "admin:accounts_invitation_changelist"
                        ),
                    },
                    {
                        "title": "Access log",
                        "icon": "history",
                        "link": reverse_lazy(
                            "admin:accounts_accessevent_changelist"
                        ),
                    },
                ],
            },
            {
                "title": "Account",
                "separator": True,
                "items": [
                    {
                        "title": "API keys",
                        "icon": "key",
                        "link": reverse_lazy("apikeys:list"),
                    },
                ],
            },
        ],
    },
}

# --- Deployment / production ---------------------------------------------
#
# Everything below is env-driven and no-ops in development. See .env.example
# and the "Production deployment" section of the README. Postgres is selected
# simply by setting DATABASE_URL=postgres://… (handled by env.db_url above).

# Cache — drives DRF rate limiting. Default is per-process in-memory; set
# REDIS_URL in production so limits are shared across gunicorn workers.
_redis_url = env("REDIS_URL", default="")
if _redis_url:
    CACHES = {"default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": _redis_url,
    }}
else:
    CACHES = {"default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
    }}

# File/static storage backends (Django's defaults; overridden below per env).
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
    },
}

# Static files: WhiteNoise serves them from the app process (no nginx needed)
# when USE_WHITENOISE is on (recommended in the Docker image).
if env.bool("USE_WHITENOISE", default=False):
    MIDDLEWARE.insert(
        MIDDLEWARE.index("django.middleware.security.SecurityMiddleware") + 1,
        "whitenoise.middleware.WhiteNoiseMiddleware",
    )
    STORAGES["staticfiles"] = {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    }

# Media files: offload to S3-compatible object storage when configured.
if env.bool("USE_S3", default=False):
    STORAGES["default"] = {
        "BACKEND": "storages.backends.s3.S3Storage",
        "OPTIONS": {
            "bucket_name": env("AWS_STORAGE_BUCKET_NAME"),
            "region_name": env("AWS_S3_REGION_NAME", default=""),
            "endpoint_url": env("AWS_S3_ENDPOINT_URL", default="") or None,
            "default_acl": env("AWS_DEFAULT_ACL", default="private"),
            "querystring_auth": True,
        },
    }

# CSRF trusted origins for HTTPS deployments (e.g. https://eval.example.org).
CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=[])

# Hardened cookie/transport settings, gated on an explicit flag (NOT `not
# DEBUG`, so tests and dev — which also run with DEBUG off — are unaffected).
# Set SECURE_DEPLOY=True behind TLS in production; each item is overridable.
if env.bool("SECURE_DEPLOY", default=False):
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_SSL_REDIRECT = env.bool("SECURE_SSL_REDIRECT", default=True)
    SESSION_COOKIE_SECURE = env.bool("SESSION_COOKIE_SECURE", default=True)
    CSRF_COOKIE_SECURE = env.bool("CSRF_COOKIE_SECURE", default=True)
    SECURE_HSTS_SECONDS = env.int("SECURE_HSTS_SECONDS", default=31536000)
    SECURE_HSTS_INCLUDE_SUBDOMAINS = env.bool(
        "SECURE_HSTS_INCLUDE_SUBDOMAINS", default=True
    )
    SECURE_HSTS_PRELOAD = env.bool("SECURE_HSTS_PRELOAD", default=True)
