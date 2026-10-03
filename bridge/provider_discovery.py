"""Opt-in catalog discovery and non-authoritative manual provider probes."""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping

from bridge.codex_auth import auth_status, validate_codex_endpoint
from bridge.network_security import strict_urlopen, validate_provider_endpoint
from bridge.provider_catalog import load_provider_catalog, load_routing_catalog, merge_model_catalog
from bridge.provider_catalog_cache import (
    MAX_CACHE_BYTES,
    cache_timestamp,
    context_window_value,
    model_ids,
    read_model_cache,
    update_model_cache,
)
from bridge.provider_errors import provider_category_for_status
from bridge.provider_transport import opencode_muse_headers
from bridge.settings import AppSettings

_CONTEXT_FIELDS = (
    "context_length",
    "context_window_tokens",
    "context_window",
    "max_context_length",
    "max_position_embeddings",
)


class _MissingCredential(ValueError):
    pass


def _endpoint(spec: Mapping[str, object], *, app_settings: AppSettings) -> str:
    endpoint = str(spec.get("api_endpoint") or spec.get("api") or "").strip().rstrip("/")
    for suffix in ("/chat/completions", "/messages"):
        if endpoint.endswith(suffix):
            endpoint = endpoint[: -len(suffix)]
    if not endpoint:
        raise ValueError("provider endpoint not configured")
    validate_provider_endpoint(endpoint, environ=app_settings.environ)
    return endpoint


def _catalog_endpoint(spec: Mapping[str, object], *, app_settings: AppSettings) -> str:
    explicit = str(spec.get("models_endpoint") or "").strip()
    if not explicit:
        return _endpoint(spec, app_settings=app_settings) + "/models"
    parts = urllib.parse.urlsplit(explicit)
    if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
        raise ValueError("models_endpoint must be an HTTP(S) URL without credentials")
    validate_provider_endpoint(explicit, environ=app_settings.environ)
    return explicit


def _headers(provider_id: str, spec: Mapping[str, object], *, app_settings: AppSettings) -> dict[str, str]:
    transport = str(spec.get("transport") or "chat_completions")
    configured = spec.get("api_key_env")
    default = "ANTHROPIC_API_KEY" if transport == "anthropic_messages" else "LLM_API_KEY"
    key_env = str(configured or default)
    key = app_settings.environ.get(key_env, "")
    if configured and not key and transport != "opencode_muse":
        raise _MissingCredential("missing provider credential")
    headers = (
        opencode_muse_headers(f"health:{provider_id}", app_settings=app_settings)
        if transport == "opencode_muse"
        else {"Accept": "application/json", "User-Agent": "SillyTavernTelegramBridge/1.0"}
    )
    if transport == "anthropic_messages":
        headers["x-api-key"] = key
        headers["anthropic-version"] = str(spec.get("anthropic_version") or "2023-06-01")
    elif key:
        headers["Authorization"] = f"Bearer {key}"
    extra = spec.get("extra_headers") or {}
    if not isinstance(extra, Mapping) or any(
        not isinstance(k, str) or not isinstance(v, str) or any(c in k + v for c in "\r\n") for k, v in extra.items()
    ):
        raise ValueError("invalid provider headers")
    headers.update(extra)
    return headers


def _failure_category(error: Exception) -> str:
    if isinstance(error, urllib.error.HTTPError):
        return provider_category_for_status(error.code)
    if isinstance(error, TimeoutError) or (
        isinstance(error, urllib.error.URLError) and isinstance(error.reason, TimeoutError)
    ):
        return "timeout"
    if isinstance(error, (urllib.error.URLError, OSError)):
        return "network"
    if isinstance(error, (ValueError, UnicodeError)):
        return "invalid_response"
    return "request_failed"


def _is_due(
    cached: Mapping[str, object],
    *,
    force: bool,
    now: float,
    attempt_field: str,
    refreshed_field: str,
    refresh_seconds: int,
) -> bool:
    if force:
        return True
    last_attempt = cache_timestamp(cached.get(attempt_field))
    if last_attempt is None:
        last_attempt = cache_timestamp(cached.get(refreshed_field))
    return last_attempt is None or not 0 <= now - last_attempt < refresh_seconds


def _context_windows_for_configured_models(
    items: list[object],
    configured_models: list[str],
) -> dict[str, int]:
    configured = set(configured_models)
    result: dict[str, int] = {}
    for item in items:
        if not isinstance(item, Mapping):
            continue
        model = item.get("id")
        if not isinstance(model, str) or model not in configured:
            continue
        for field in _CONTEXT_FIELDS:
            value = context_window_value(item.get(field))
            if value is not None:
                result[model] = value
                break
    return result


def refresh_model_catalog(
    force: bool = False,
    provider_id: str | None = None,
    *,
    metadata_only: bool = False,
    app_settings: AppSettings,
) -> tuple[dict, int, int]:
    providers = load_provider_catalog(app_settings=app_settings)
    cache = read_model_cache(app_settings=app_settings)
    updates: dict[str, dict[str, object]] = {}
    refreshed = failed = 0

    for current_id, raw_spec in providers.items():
        if provider_id is not None and current_id != provider_id:
            continue
        if not isinstance(raw_spec, Mapping):
            failed += 1
            continue
        spec = raw_spec
        discover_models = spec.get("discover_models") is True and not metadata_only
        discover_metadata = spec.get("discover_model_metadata") is True
        if metadata_only:
            if not discover_metadata:
                continue
        elif not discover_models and not discover_metadata:
            continue

        cached = cache.get(current_id, {})
        if str(spec.get("transport") or "") == "openai_codex":
            if discover_models and not model_ids(spec.get("models")) and not model_ids(cached.get("models")):
                failed += 1
            continue

        now = time.time()
        model_due = discover_models and _is_due(
            cached,
            force=force,
            now=now,
            attempt_field="last_attempt_at",
            refreshed_field="refreshed_at",
            refresh_seconds=app_settings.model_refresh_seconds,
        )
        metadata_due = discover_metadata and _is_due(
            cached,
            force=force,
            now=now,
            attempt_field="metadata_last_attempt_at",
            refreshed_field="metadata_refreshed_at",
            refresh_seconds=app_settings.model_refresh_seconds,
        )
        if not model_due and not metadata_due:
            continue

        updated: dict[str, object] = {}
        if model_due:
            updated["last_attempt_at"] = now
        if metadata_due:
            updated["metadata_last_attempt_at"] = now

        error: str | None = None
        try:
            endpoint = _catalog_endpoint(spec, app_settings=app_settings)
            headers = _headers(current_id, spec, app_settings=app_settings)
        except _MissingCredential:
            error = "missing_credential"
        except Exception:
            error = "configuration"

        if error is None:
            try:
                request = urllib.request.Request(  # noqa: S310 -- validated endpoint; DNS-pinned strict_urlopen only
                    endpoint,
                    headers=headers,
                    method="GET",
                )
                with strict_urlopen(request, timeout=30, environ=app_settings.environ) as response:
                    content = response.read(MAX_CACHE_BYTES + 1)
                if len(content) > MAX_CACHE_BYTES:
                    raise ValueError("catalog response exceeds size limit")
                payload = json.loads(content)
                if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
                    raise ValueError("invalid models response")
                items = payload["data"]
                models = model_ids([item.get("id") for item in items if isinstance(item, Mapping)])
                if not models:
                    error = "empty_catalog"
                else:
                    if model_due:
                        updated.update(models=models, refreshed_at=now, last_error=None)
                    if metadata_due:
                        updated.update(
                            model_context_window_tokens=_context_windows_for_configured_models(
                                items,
                                model_ids(spec.get("models")),
                            ),
                            metadata_refreshed_at=now,
                            metadata_last_error=None,
                        )
            except Exception as exc:
                error = _failure_category(exc)

        if error is not None:
            if model_due:
                updated["last_error"] = error
            if metadata_due:
                updated["metadata_last_error"] = error
            failed += 1
            logging.info("Provider catalog refresh failed for provider %s (%s)", current_id, error)
        else:
            refreshed += 1

        cache[current_id] = {**cached, **updated}
        updates[current_id] = updated

    update_model_cache(updates, app_settings=app_settings)
    return {"providers": merge_model_catalog(providers, cache)}, refreshed, failed


def probe_provider(provider_id: str, raw_spec: object, *, app_settings: AppSettings) -> tuple[str, str, str]:
    """Probe one provider without changing its runtime health or discovery cache."""
    if not isinstance(raw_spec, Mapping):
        return provider_id, provider_id, "configuration error"
    spec = raw_spec
    name = str(spec.get("name") or provider_id)
    transport = str(spec.get("transport") or "chat_completions")
    try:
        if transport == "openai_codex":
            endpoint = validate_codex_endpoint(spec, environ=app_settings.environ)
            validate_provider_endpoint(endpoint, environ=app_settings.environ)
            state = auth_status(app_settings.codex_oauth_file)
            status = "authenticated" if state["authenticated"] else "not logged in"
            if state["authenticated"] and state["expiring"]:
                status = "authenticated (refresh needed)"
            return provider_id, name, status
        if not (spec.get("api_endpoint") or spec.get("api")):
            return provider_id, name, "not configured"
        endpoint = _endpoint(spec, app_settings=app_settings)
        headers = _headers(provider_id, spec, app_settings=app_settings)
    except _MissingCredential:
        return provider_id, name, "missing credential"
    except Exception:
        return provider_id, name, "configuration error"
    try:
        if str(spec.get("health_check") or "").casefold() == "chat_completion":
            models = model_ids(spec.get("models"))
            model = str(spec.get("model") or (models[0] if models else ""))
            if not model:
                return provider_id, name, "no inference probe model configured"
            body = {
                "model": model,
                "messages": [{"role": "user", "content": "Reply OK."}],
                "max_tokens": 8,
                "temperature": 0,
                "stream": True,
            }
            suffix = "/messages" if transport == "anthropic_messages" else "/chat/completions"
            request = urllib.request.Request(  # noqa: S310 -- validated endpoint; DNS-pinned strict_urlopen only
                endpoint + suffix,
                data=json.dumps(body).encode(),
                headers={**headers, "Accept": "text/event-stream", "Content-Type": "application/json"},
                method="POST",
            )
            with strict_urlopen(request, timeout=30, environ=app_settings.environ) as response:
                first_byte = response.read(1)
            status = "inference stream opened (completion not validated)" if first_byte else "inference stream empty"
            return provider_id, name, status
        request = urllib.request.Request(  # noqa: S310 -- validated endpoint; DNS-pinned strict_urlopen only
            _catalog_endpoint(spec, app_settings=app_settings),
            headers=headers,
            method="GET",
        )
        with strict_urlopen(request, timeout=10, environ=app_settings.environ) as response:
            return provider_id, name, f"catalog reachable (HTTP {response.status})"
    except urllib.error.HTTPError as exc:
        return (
            provider_id,
            name,
            f"reachable (HTTP {exc.code}; {provider_category_for_status(exc.code).replace('_', ' ')})",
        )
    except Exception as exc:
        return provider_id, name, f"probe failed ({_failure_category(exc)})"


def provider_health_checks(provider_id: str | None = None, *, app_settings: AppSettings) -> list[tuple[str, str, str]]:
    providers = load_provider_catalog(app_settings=app_settings)
    known = merge_model_catalog(providers, read_model_cache(app_settings=app_settings))
    return [
        probe_provider(current_id, known.get(current_id, raw_spec), app_settings=app_settings)
        for current_id, raw_spec in providers.items()
        if provider_id is None or current_id == provider_id
    ]


def get_model_groups(
    *, app_settings: AppSettings, refresh: bool = True
) -> dict[str, tuple[str, list[tuple[str, str]], bool]]:
    """The menu uses exactly the same persisted IDs as routing, without pre-pagination truncation."""
    if refresh:
        refresh_model_catalog(app_settings=app_settings)
    providers = load_routing_catalog(app_settings=app_settings)
    supported = {
        "chat_completions",
        "openai",
        "openai_compatible",
        "anthropic_messages",
        "opencode_muse",
        "openai_codex",
    }
    groups: dict[str, tuple[str, list[tuple[str, str]], bool]] = {}
    for provider_id, provider in providers.items():
        if not isinstance(provider, Mapping):
            continue
        models = [(model, f"{provider_id}::{model}") for model in model_ids(provider.get("models"))]
        if models:
            adapter = str(provider.get("adapter") or provider.get("transport") or "")
            groups[provider_id] = (str(provider.get("name") or provider_id), models, adapter in supported)
    return groups
