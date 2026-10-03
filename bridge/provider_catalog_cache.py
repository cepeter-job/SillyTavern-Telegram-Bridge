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
MAX_MODEL_CONTEXT_WINDOWS = 500
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
_MODEL_FIELDS = frozenset({"models", "refreshed_at", "last_attempt_at", "last_error"})
_METADATA_FIELDS = frozenset(
    {
        "model_context_window_tokens",
        "metadata_refreshed_at",
        "metadata_last_attempt_at",
        "metadata_last_error",
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


def context_window_value(raw: object) -> int | None:
    if isinstance(raw, bool) or not isinstance(raw, (str, int, float)):
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError, OverflowError):
        return None
    return value if 4096 <= value <= 1_000_000 else None


def model_context_windows(raw: object) -> dict[str, int]:
    if not isinstance(raw, Mapping):
        return {}
    result: dict[str, int] = {}
    for model, value in raw.items():
        if len(result) >= MAX_MODEL_CONTEXT_WINDOWS:
            break
        if model_ids([model]) != [model]:
            continue
        parsed = context_window_value(value)
        if parsed is not None:
            result[model] = parsed
    return result


def cache_timestamp(value: object) -> float | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except OverflowError:
        return None
    return number if math.isfinite(number) and number >= 0 else None


def _clean_cache_entry(entry: Mapping[str, object]) -> dict[str, object]:
    clean: dict[str, object] = {"models": model_ids(entry.get("models"))}
    if "model_context_window_tokens" in entry and isinstance(entry.get("model_context_window_tokens"), Mapping):
        clean["model_context_window_tokens"] = model_context_windows(entry.get("model_context_window_tokens"))
    for name in ("refreshed_at", "last_attempt_at", "metadata_refreshed_at", "metadata_last_attempt_at"):
        stamp = cache_timestamp(entry.get(name))
        if stamp is not None:
            clean[name] = stamp
    for name in ("last_error", "metadata_last_error"):
        error = entry.get(name)
        if isinstance(error, str) and error in _ERRORS:
            clean[name] = error
    return clean


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
        result[provider] = _clean_cache_entry(entry)
        if len(result) >= 1024:
            break
    return result


def _merge_dimension(
    merged: dict[str, object],
    previous: Mapping[str, object],
    incoming: Mapping[str, object],
    *,
    fields: frozenset[str],
    attempt_field: str,
) -> None:
    if not any(field in incoming for field in fields):
        return
    incoming_attempt = cache_timestamp(incoming.get(attempt_field)) or 0
    previous_attempt = cache_timestamp(previous.get(attempt_field)) or 0
    if incoming_attempt < previous_attempt:
        return
    for field in fields:
        if field in incoming:
            merged[field] = incoming[field]


def update_model_cache(updates: Mapping[str, dict[str, object]], *, app_settings: AppSettings) -> None:
    if not updates:
        return
    temporary: str | None = None
    try:
        with _CACHE_LOCK:
            cache = read_model_cache(app_settings=app_settings)
            for provider, entry in updates.items():
                if not isinstance(provider, str) or not isinstance(entry, Mapping):
                    continue
                previous = cache.get(provider, {})
                merged: dict[str, object] = dict(previous)
                _merge_dimension(
                    merged,
                    previous,
                    entry,
                    fields=_MODEL_FIELDS,
                    attempt_field="last_attempt_at",
                )
                _merge_dimension(
                    merged,
                    previous,
                    entry,
                    fields=_METADATA_FIELDS,
                    attempt_field="metadata_last_attempt_at",
                )
                cache[provider] = _clean_cache_entry(merged)
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
