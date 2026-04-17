from django.contrib import admin
from django.urls import path

from . import views

app_name = "apikeys"

urlpatterns = [
    path("", admin.site.admin_view(views.list_keys), name="list"),
    path("new/", admin.site.admin_view(views.create_key), name="create"),
    path(
        "<uuid:key_id>/show/",
        admin.site.admin_view(views.show_key),
        name="show_key",
    ),
    path(
        "<uuid:key_id>/rotate/",
        admin.site.admin_view(views.rotate_key),
        name="rotate",
    ),
    path(
        "<uuid:key_id>/revoke/",
        admin.site.admin_view(views.revoke_key),
        name="revoke",
    ),
    path(
        "<uuid:key_id>/events/",
        admin.site.admin_view(views.key_events),
        name="events",
    ),
]
