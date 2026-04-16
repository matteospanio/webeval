from django.urls import path

from . import views

app_name = "experiments"

urlpatterns = [
    path("<slug:slug>/reproducibility.json", views.repro_json, name="repro_json"),
    path("<slug:slug>/printable/", views.printable, name="printable"),
]
