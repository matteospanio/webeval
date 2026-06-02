from django.urls import path

from . import views

app_name = "survey"

urlpatterns = [
    # No public landing page — each survey is shared via /s/<slug>/ directly.
    path("s/<slug:slug>/", views.consent, name="consent"),
    path("s/<slug:slug>/access/", views.access, name="access"),
    path("s/<slug:slug>/screening/", views.screening, name="screening"),
    path("s/<slug:slug>/screened-out/", views.screened_out, name="screened_out"),
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
    path("s/<slug:slug>/resume/<str:token>/", views.resume, name="resume"),
    path("s/<slug:slug>/withdraw/<str:token>/", views.withdraw, name="withdraw"),
]
