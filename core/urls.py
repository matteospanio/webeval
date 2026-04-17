"""URL configuration for the webeval project."""
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

from core.views import database_export

urlpatterns = [
    # Mount the database export ahead of ``admin.site.urls`` so the more
    # specific path wins the match.
    path(
        "admin/database-export.json",
        database_export,
        name="webeval_database_export",
    ),
    path("admin/", admin.site.urls),
    path("experiments/", include("experiments.urls")),
    path("", include("survey.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
