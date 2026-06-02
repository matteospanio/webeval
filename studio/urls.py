"""Researcher studio URLs."""
from __future__ import annotations

from django.urls import path

from . import views

app_name = "studio"

urlpatterns = [
    path("", views.studies, name="studies"),
    path("new/", views.study_create, name="study_create"),
    path("<slug:slug>/", views.study_overview, name="study_overview"),
    path("<slug:slug>/access/", views.study_access, name="study_access"),
    path("<slug:slug>/answers.csv", views.answers_csv, name="answers_csv"),
    path(
        "<slug:slug>/demographics.csv",
        views.demographics_csv,
        name="demographics_csv",
    ),
    path(
        "<slug:slug>/completion-codes.csv",
        views.completion_codes_csv,
        name="completion_codes_csv",
    ),
    path(
        "<slug:slug>/pairwise-answers.csv",
        views.pairwise_csv,
        name="pairwise_csv",
    ),
    path("<slug:slug>/export.zip", views.export_zip, name="export_zip"),
    path("<slug:slug>/chart.svg", views.chart, name="chart"),
]
