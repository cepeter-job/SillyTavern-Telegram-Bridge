"""Bounded discovery-cache I/O; cached values can never supply provider credentials."""

from __future__ import annotations

import json
import logging
import math
import os
import tempfile
import threading
from collections.abc import Mapping

from bridge.settings import AppSettings

MAX_CACHE_BYTES = 2_000_000
_CACHE_LOCK = threading.RLock()
_ERRORS = frozenset(
    {
        "configuration",
        "missing_credential",
        "empty_catalog",
        "invalid_response",
        "timeout",
        "network",
        "rate_limit",
        "authentication",
        "credits",
        "model_unavailable",
        "request_too_large",
        "provider_unavailable",
        "provider_rejected",
        "request_failed",
    }
)


def model_ids(raw: object) -> list[str]:
    if not isinstance(raw, (list, tuple)):
        return []
    return list(
        dict.fromkeys(
            item
            for item in raw
            if isinstance(item, str)
            and 0 < len(item) <= 200
            and not any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in item)
        )
    )


def cache_timestamp(value: object) -> float | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except OverflowError:
        return None
    return number if math.isfinite(number) and number >= 0 else None


def read_model_cache(*, app_settings: AppSettings) -> dict[str, dict[str, object]]:
    try:
        with app_settings.model_cache_file.open("rb") as source:
            content = source.read(MAX_CACHE_BYTES + 1)
        if len(content) > MAX_CACHE_BYTES:
            return {}
        raw = json.loads(content)
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    result: dict[str, dict[str, object]] = {}
    for provider, entry in raw.items():
        if not isinstance(provider, str) or not isinstance(entry, dict):
            continue
        clean: dict[str, object] = {"models": model_ids(entry.get("models"))}
        for name in ("refreshed_at", "last_attempt_at"):
            stamp = cache_timestamp(entry.get(name))
            if stamp is not None:
                clean[name] = stamp
        error = entry.get("last_error")
        if isinstance(error, str) and error in _ERRORS:
            clean["last_error"] = error
        result[provider] = clean
        if len(result) >= 1024:
            break
    return result


def update_model_cache(updates: Mapping[str, dict[str, object]], *, app_settings: AppSettings) -> None:
    if not updates:
        return
    temporary: str | None = None
    try:
        with _CACHE_LOCK:
            cache = read_model_cache(app_settings=app_settings)
            for provider, entry in updates.items():
                previous = cache.get(provider, {})
                if (cache_timestamp(entry.get("last_attempt_at")) or 0) < (
                    cache_timestamp(previous.get("last_attempt_at")) or 0
                ):
                    continue
                cache[provider] = {**previous, **entry}
            data = json.dumps(cache, sort_keys=True, indent=2).encode()
            if len(data) > MAX_CACHE_BYTES:
                raise ValueError("discovery cache exceeds size limit")
            destination = app_settings.model_cache_file
            destination.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=destination.parent, prefix=".models-", delete=False) as target:
                temporary = target.name
                os.chmod(temporary, 0o600)
                target.write(data)
                target.flush()
                os.fsync(target.fileno())
            os.replace(temporary, destination)
            temporary = None
    except (OSError, ValueError):
        logging.warning("Could not persist the model catalog cache; configured routes remain available")
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except OSError:
                pass
