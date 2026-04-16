from django.urls import path

from . import views

app_name = "survey"

urlpatterns = [
    # No public landing page — each survey is shared via /s/<slug>/ directly.
    path("s/<slug:slug>/", views.consent, name="consent"),
    path("s/<slug:slug>/instructions/", views.instructions, name="instructions"),
    path("s/<slug:slug>/audio-check/", views.audio_check, name="audio_check"),
    path("s/<slug:slug>/play/", views.play, name="play"),
    path("s/<slug:slug>/compare/", views.pairwise_play, name="pairwise_play"),
    path(
        "s/<slug:slug>/listen/<int:assignment_id>/",
        views.record_listen,
        name="record_listen",
    ),
    path(
        "s/<slug:slug>/listen-pair/<int:pair_id>/",
        views.record_listen_pair,
        name="record_listen_pair",
    ),
    path("s/<slug:slug>/demographics/", views.demographics, name="demographics"),
    path("s/<slug:slug>/thanks/", views.thanks, name="thanks"),
]
