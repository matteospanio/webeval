"""Outbound webhooks for downstream pipelines.

Best-effort, synchronous, and stdlib-only (``urllib`` + ``hmac``) so there's
no broker or extra dependency. Each delivery is HMAC-signed
(``X-Webhook-Signature: sha256=…``) so receivers can verify authenticity, and
delivery never raises into the participant flow. Per-hook status is recorded
for debugging; for high-volume studies, point a webhook at a queue/relay.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import urllib.error
import urllib.request

from django.utils import timezone

_TIMEOUT_SECONDS = 4


def _post(url: str, body: bytes, headers: dict) -> int:
    """POST ``body`` to ``url``; return the HTTP status. Mockable in tests."""
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as resp:
        return resp.status


def _payload(session, event: str) -> dict:
    return {
        "event": event,
        "experiment": session.experiment.slug,
        "session_id": str(session.id),
        "submitted_at": session.submitted_at.isoformat()
        if session.submitted_at
        else None,
        "external_id": session.external_id,
        "completion_code": session.completion_code,
    }


def deliver_event(session, event: str) -> int:
    """Deliver ``event`` for ``session`` to every active webhook; return the
    number successfully delivered. Best-effort — records status per hook."""
    from experiments.models import Webhook

    hooks = list(
        Webhook.objects.filter(
            experiment=session.experiment, event=event, is_active=True
        )
    )
    if not hooks:
        return 0

    body = json.dumps(_payload(session, event)).encode("utf-8")
    delivered = 0
    for hook in hooks:
        signature = hmac.new(
            hook.secret.encode("utf-8"), body, hashlib.sha256
        ).hexdigest()
        headers = {
            "Content-Type": "application/json",
            "X-Webhook-Event": event,
            "X-Webhook-Signature": f"sha256={signature}",
        }
        status: int | None = None
        error = ""
        try:
            status = _post(hook.url, body, headers)
            delivered += 1
        except urllib.error.HTTPError as exc:
            status = exc.code
            error = str(exc)[:300]
        except Exception as exc:  # pragma: no cover - network failure paths
            error = str(exc)[:300]
        Webhook.objects.filter(pk=hook.pk).update(
            last_delivered_at=timezone.now(), last_status=status, last_error=error
        )
    return delivered
