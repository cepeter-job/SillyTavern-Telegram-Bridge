"""Provider catalog infrastructure adapter."""

from __future__ import annotations

import logging
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import yaml

from bridge.provider_catalog_cache import cache_timestamp, model_ids, read_model_cache
from bridge.settings import AppSettings


def load_provider_catalog(path: Path | None = None, *, app_settings: AppSettings) -> Mapping[str, object]:
    if path is None:
        path = app_settings.provider_config_file
    try:
        config = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    except Exception:
        logging.warning("Could not read provider catalog", exc_info=True)
        return {}
    providers = config.get("providers") if isinstance(config, dict) else None
    return providers if isinstance(providers, dict) else {}


def merge_model_catalog(
    providers: Mapping[str, object], cache: Mapping[str, dict[str, object]]
) -> dict[str, dict[str, object]]:
    """Only explicitly opted-in model IDs may cross from discovery into routing."""
    result: dict[str, dict[str, object]] = {}
    for provider_id, raw_spec in providers.items():
        if not isinstance(raw_spec, Mapping):
            continue
        spec = dict(raw_spec)
        models = model_ids(spec.get("models"))
        if spec.get("discover_models") is True:
            models.extend(model_ids(cache.get(provider_id, {}).get("models")))
        spec["models"] = list(dict.fromkeys(models))
        result[str(provider_id)] = spec
    return result


def load_routing_catalog(*, app_settings: AppSettings) -> Mapping[str, object]:
    """Read YAML plus cached model IDs, without network I/O or health-based routing."""
    return merge_model_catalog(
        load_provider_catalog(app_settings=app_settings), read_model_cache(app_settings=app_settings)
    )


def _context_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    try:
        parsed = int(value)
    except ValueError:
        return None
    return parsed if 4096 <= parsed <= 1_000_000 else None


def _context_float(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    try:
        parsed = float(value)
    except ValueError:
        return None
    return parsed if 1.0 <= parsed <= 8.0 else None


def context_metadata_for_model(model_selection: str, *, app_settings: AppSettings) -> dict[str, object]:
    """Resolve optional provider/model context metadata without guessing routes."""
    selection = str(model_selection or "").strip()
    if not selection or not app_settings.provider_config_file.is_file():
        return {}
    catalog = load_routing_catalog(app_settings=app_settings)
    provider_id = ""
    model_id = selection
    spec: Mapping[str, object] | None = None
    if "::" in selection:
        parts = selection.split("::")
        if len(parts) != 2 or not all(parts):
            return {}
        provider_id, model_id = parts
        candidate = catalog.get(provider_id)
        if isinstance(candidate, Mapping) and model_id in model_ids(candidate.get("models")):
            spec = candidate
    else:
        matches = [
            (str(candidate_id), candidate)
            for candidate_id, candidate in catalog.items()
            if isinstance(candidate, Mapping) and model_id in model_ids(candidate.get("models"))
        ]
        if len(matches) == 1:
            provider_id, spec = matches[0]
    if spec is None:
        return {}

    window = _context_int(spec.get("context_window_tokens"))
    chars_per_token = _context_float(spec.get("token_estimate_chars_per_token"))
    source = "provider" if window is not None or chars_per_token is not None else ""

    model_windows = spec.get("model_context_window_tokens")
    if isinstance(model_windows, Mapping):
        window_override = _context_int(model_windows.get(model_id))
        if window_override is not None:
            window = window_override
            source = "provider-model"

    model_estimates = spec.get("model_token_estimate_chars_per_token")
    if isinstance(model_estimates, Mapping):
        estimate_override = _context_float(model_estimates.get(model_id))
        if estimate_override is not None:
            chars_per_token = estimate_override
            source = "provider-model"

    return {
        "provider_id": provider_id,
        "model_id": model_id,
        "window_tokens": window,
        "chars_per_token": chars_per_token,
        "source": source,
    }


@dataclass(frozen=True)
class CatalogStatus:
    configured: int
    discovered: int
    refreshed_at: float | None
    last_attempt_at: float | None
    last_error: str | None
    stale: bool


def get_catalog_status(provider_id: str, *, app_settings: AppSettings) -> CatalogStatus:
    providers = load_provider_catalog(app_settings=app_settings)
    raw = providers.get(provider_id)
    spec = raw if isinstance(raw, Mapping) else {}
    configured = set(model_ids(spec.get("models")))
    entry = (
        read_model_cache(app_settings=app_settings).get(provider_id, {}) if spec.get("discover_models") is True else {}
    )
    discovered = set(model_ids(entry.get("models"))) - configured
    refreshed = cache_timestamp(entry.get("refreshed_at"))
    error = entry.get("last_error")
    return CatalogStatus(
        len(configured),
        len(discovered),
        refreshed,
        cache_timestamp(entry.get("last_attempt_at")),
        error if isinstance(error, str) else None,
        spec.get("discover_models") is True
        and (refreshed is None or not 0 <= time.time() - refreshed < app_settings.model_refresh_seconds),
    )
