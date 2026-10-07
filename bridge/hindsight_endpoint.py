"""Validate the optional loopback endpoint and prepare supported SDK proxy routing."""

from __future__ import annotations

import ipaddress
import os
import threading
from urllib.parse import urlsplit

from bridge.limits import HINDSIGHT_DEFAULT_URL
from bridge.settings import AppSettings

_PREPARATION_LOCK = threading.Lock()
_PREPARED_HOST: str | None = None


def validated_hindsight_base_url(value: str) -> tuple[str, str]:
    raw = str(value or "").strip().rstrip("/")
    message = "Hindsight SDK endpoint must use a numeric loopback origin"
    try:
        parsed = urlsplit(raw)
        _ = parsed.port
    except ValueError as exc:
        raise ValueError(message) from exc
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ValueError(message)
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError as exc:
        raise ValueError(message) from exc
    if not address.is_loopback:
        raise ValueError(message)
    return raw, address.compressed


def _ensure_hindsight_loopback_proxy_bypass(host: str) -> None:
    values: list[str] = []
    seen: set[str] = set()
    for name in ("NO_PROXY", "no_proxy"):
        for item in os.environ.get(name, "").split(","):
            entry = item.strip()
            if entry and entry not in seen:
                seen.add(entry)
                values.append(entry)
    for required in (host, "127.0.0.1", "::1", "[::1]"):
        if required not in seen:
            seen.add(required)
            values.append(required)
    combined = ",".join(values)
    for name in ("NO_PROXY", "no_proxy"):
        if os.environ.get(name) != combined:
            os.environ[name] = combined


def prepare_hindsight_endpoint(app_settings: AppSettings) -> tuple[str, str]:
    """An explicit startup/configuration event prepares the actual process environment."""
    global _PREPARED_HOST
    endpoint = validated_hindsight_base_url(app_settings.environ.get("HINDSIGHT_API_URL", HINDSIGHT_DEFAULT_URL))
    with _PREPARATION_LOCK:
        _ensure_hindsight_loopback_proxy_bypass(endpoint[1])
        _PREPARED_HOST = endpoint[1]
    return endpoint


def prepare_compatible_hindsight_endpoint(app_settings: AppSettings) -> str:
    """One-shot background clients reuse startup routing, with one bounded legacy slot."""
    global _PREPARED_HOST
    base_url, host = validated_hindsight_base_url(app_settings.environ.get("HINDSIGHT_API_URL", HINDSIGHT_DEFAULT_URL))
    with _PREPARATION_LOCK:
        if _PREPARED_HOST != host:
            _ensure_hindsight_loopback_proxy_bypass(host)
            _PREPARED_HOST = host
    return base_url
