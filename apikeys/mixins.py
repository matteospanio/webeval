"""DRF view mixin that appends a ``used`` event after every authenticated call."""
from __future__ import annotations

from django.utils import timezone

from . import _request_meta
from .models import APIKey, APIKeyEvent


class LogAPIKeyUsageMixin:
    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        api_key = getattr(request, "auth", None)
        if isinstance(api_key, APIKey):
            meta = _request_meta.extract(request)
            now = timezone.now()
            APIKeyEvent.objects.create(
                api_key=api_key,
                user=api_key.user,
                event_type=APIKeyEvent.Event.USED,
                ip_address=meta.ip_address,
                user_agent=meta.user_agent,
                request_method=(request.method or "")[:10],
                request_path=(request.path or "")[:500],
                response_status=getattr(response, "status_code", None),
            )
            APIKey.objects.filter(pk=api_key.pk).update(last_used_at=now)
        return response
