"""URL wiring for the stimulus upload REST API.

Mounted under ``/api/v1/`` in :mod:`core.urls`. Kept in a separate module
from ``experiments/urls.py`` so the staff API surface is independent of the
participant-facing experiment URLs.
"""
from __future__ import annotations

from django.urls import path

from .api import PairwiseAnswersView, PromptUploadView, StimulusUploadView

urlpatterns = [
    path(
        "experiments/<slug:slug>/stimuli/",
        StimulusUploadView.as_view(),
        name="api_stimulus_upload",
    ),
    path(
        "experiments/<slug:slug>/prompts/",
        PromptUploadView.as_view(),
        name="api_prompt_upload",
    ),
    path(
        "experiments/<slug:slug>/pairwise-answers/",
        PairwiseAnswersView.as_view(),
        name="api_pairwise_answers",
    ),
]
