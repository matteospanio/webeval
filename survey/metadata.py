"""Metadata helpers: extract device/browser from a request and resolve the
client IP to a country code via an offline MaxMind GeoLite2 database.

Design goals:
* No external network calls.
* Gracefully degrades when the MaxMind database is missing (returns ``None``
  for the country code) so local dev and tests never fail on setup.
* Stores only the country code — no raw IP, no coarser geolocation.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from django.conf import settings
from django.http import HttpRequest

logger = logging.getLogger(__name__)


@dataclass
class ClientMetadata:
    device_type: str
    browser_family: str
    country_code: str  # empty string when unknown


def extract_metadata(request: HttpRequest) -> ClientMetadata:
    ua_string = request.META.get("HTTP_USER_AGENT", "") or ""
    device_type, browser_family = _parse_user_agent(ua_string)
    ip = _client_ip(request)
    country = _lookup_country(ip) or ""
    return ClientMetadata(
        device_type=device_type,
        browser_family=browser_family[:64],
        country_code=country,
    )


def _parse_user_agent(ua_string: str) -> tuple[str, str]:
    try:
        from user_agents import parse
    except Exception:  # pragma: no cover - import guard
        return ("unknown", "")

    try:
        ua = parse(ua_string)
    except Exception:
        return ("unknown", "")

    if ua.is_bot:
        device = "bot"
    elif ua.is_mobile:
        device = "mobile"
    elif ua.is_tablet:
        device = "tablet"
    elif ua.is_pc:
        device = "desktop"
    else:
        device = "unknown"
    browser = getattr(ua.browser, "family", "") or ""
    return (device, browser)


def _client_ip(request: HttpRequest) -> str | None:
    xff = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if xff:
        return xff.split(",")[0].strip() or None
    return request.META.get("REMOTE_ADDR") or None


def _lookup_country(ip: str | None) -> str | None:
    path = getattr(settings, "GEOIP_PATH", None)
    if not ip or not path or not os.path.exists(path):
        return None
    try:
        import geoip2.database
    except Exception:  # pragma: no cover - import guard
        return None
    try:
        with geoip2.database.Reader(path) as reader:
            return reader.country(ip).country.iso_code
    except Exception:
        logger.debug("GeoIP lookup failed for %s", ip, exc_info=True)
        return None
