"""Auth + invitation URLs for the accounts app."""
from __future__ import annotations

from django.conf import settings
from django.contrib.auth import views as auth_views
from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path(
        "login/",
        auth_views.LoginView.as_view(
            template_name="accounts/login.html",
            extra_context={
                "registration_enabled": settings.ACCOUNTS_ALLOW_REGISTRATION
            },
        ),
        name="login",
    ),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("register/", views.register, name="register"),
    path("profile/", views.profile, name="profile"),
    path("invite/<str:token>/", views.invite_accept, name="invite_accept"),
]
