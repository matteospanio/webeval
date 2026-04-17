"""Self-service staff views for managing API keys.

Mounted under ``/admin/api-keys/`` by :mod:`apikeys.admin_urls`. All views
require ``is_staff`` and are CSRF-protected. Non-superusers can only
see/act on their own keys; superusers can additionally browse every
user's keys and revoke them.

The freshly-generated raw key is shown exactly once: it's stashed in the
Django session under ``apikeys.raw.<uuid>`` at creation/rotation time and
popped by the ``show_key`` view on first GET.
"""
from __future__ import annotations

from django.contrib import admin, messages
from django.core.paginator import Paginator
from django.http import Http404, HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from . import _request_meta
from .forms import CreateAPIKeyForm
from .models import APIKey, APIKeyEvent
from .scopes import SCOPES

RAW_SESSION_PREFIX = "apikeys.raw."


def _admin_context(request, **extra):
    return {**admin.site.each_context(request), **extra}


def _flash_raw_key(request, api_key: APIKey, raw: str) -> None:
    request.session[f"{RAW_SESSION_PREFIX}{api_key.pk}"] = raw


def _pop_raw_key(request, api_key_id) -> str | None:
    return request.session.pop(f"{RAW_SESSION_PREFIX}{api_key_id}", None)


def _get_owned_key_or_403(request, key_id):
    key = get_object_or_404(APIKey, pk=key_id)
    if key.user_id != request.user.id and not request.user.is_superuser:
        raise Http404()
    return key


def _log(request, api_key: APIKey, event_type: str, **detail) -> None:
    meta = _request_meta.extract(request)
    APIKeyEvent.objects.create(
        api_key=api_key,
        user=api_key.user,
        event_type=event_type,
        ip_address=meta.ip_address,
        user_agent=meta.user_agent,
        request_method=(request.method or "")[:10],
        request_path=(request.path or "")[:500],
        detail=detail or {},
    )


def list_keys(request):
    show_all = request.GET.get("scope") == "all" and request.user.is_superuser
    qs = APIKey.objects.select_related("user")
    if not show_all:
        qs = qs.filter(user=request.user)
    context = _admin_context(
        request,
        title="API keys",
        keys=list(qs),
        show_all=show_all,
        can_show_all=request.user.is_superuser,
        scopes=SCOPES,
    )
    return render(request, "admin/apikeys/list.html", context)


@require_http_methods(["GET", "POST"])
def create_key(request):
    if request.method == "POST":
        form = CreateAPIKeyForm(request.POST)
        if form.is_valid():
            api_key, raw = APIKey.generate(
                user=request.user,
                name=form.cleaned_data["name"],
                scopes=form.cleaned_data["scopes"],
                expires_at=form.cleaned_data.get("expires_at"),
            )
            _log(request, api_key, APIKeyEvent.Event.CREATED)
            _flash_raw_key(request, api_key, raw)
            return HttpResponseRedirect(
                reverse("apikeys:show_key", args=[api_key.pk])
            )
    else:
        form = CreateAPIKeyForm()
    return render(
        request,
        "admin/apikeys/create.html",
        _admin_context(request, title="Create API key", form=form),
    )


def show_key(request, key_id):
    api_key = _get_owned_key_or_403(request, key_id)
    raw = _pop_raw_key(request, key_id)
    if raw is None:
        messages.warning(
            request,
            "The raw key is only shown once at creation or rotation time.",
        )
        return HttpResponseRedirect(reverse("apikeys:list"))
    return render(
        request,
        "admin/apikeys/show_key.html",
        _admin_context(request, title="New API key", api_key=api_key, raw=raw),
    )


@require_POST
def rotate_key(request, key_id):
    old = _get_owned_key_or_403(request, key_id)
    if old.revoked_at is not None:
        messages.error(request, "Can't rotate a revoked key; create a new one instead.")
        return HttpResponseRedirect(reverse("apikeys:list"))
    new, raw = APIKey.generate(
        user=old.user,
        name=old.name,
        scopes=old.scopes,
        expires_at=old.expires_at,
    )
    now = timezone.now()
    old.revoked_at = now
    old.save(update_fields=["revoked_at"])
    _log(request, old, APIKeyEvent.Event.ROTATED, new_key_id=str(new.pk))
    _log(request, new, APIKeyEvent.Event.CREATED, rotated_from=str(old.pk))
    _flash_raw_key(request, new, raw)
    return HttpResponseRedirect(reverse("apikeys:show_key", args=[new.pk]))


@require_http_methods(["GET", "POST"])
def revoke_key(request, key_id):
    api_key = _get_owned_key_or_403(request, key_id)
    if request.method == "POST":
        if api_key.revoked_at is None:
            api_key.revoked_at = timezone.now()
            api_key.save(update_fields=["revoked_at"])
            _log(request, api_key, APIKeyEvent.Event.REVOKED)
            messages.success(request, f"Revoked '{api_key.name}'.")
        return HttpResponseRedirect(reverse("apikeys:list"))
    return render(
        request,
        "admin/apikeys/confirm_revoke.html",
        _admin_context(request, title="Revoke API key", api_key=api_key),
    )


def key_events(request, key_id):
    api_key = _get_owned_key_or_403(request, key_id)
    events = api_key.events.select_related("user").order_by("-created_at")
    page = Paginator(events, 50).get_page(request.GET.get("page"))
    return render(
        request,
        "admin/apikeys/events.html",
        _admin_context(
            request,
            title=f"Events for {api_key.name}",
            api_key=api_key,
            page_obj=page,
        ),
    )
