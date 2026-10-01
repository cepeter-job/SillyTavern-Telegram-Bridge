"""Compact provider diagnostics; rendering never probes or changes circuit state."""

from __future__ import annotations

import math
import time
from collections.abc import Mapping

from bridge.port_contracts import ProviderPolicy
from bridge.provider_catalog import get_catalog_status, load_routing_catalog
from bridge.provider_catalog_cache import model_ids
from bridge.settings import AppSettings


def observation_age(stamp: float | None) -> str:
    if stamp is None:
        return "never"
    seconds = max(0, int(time.time() - stamp))
    if seconds < 60:
        return f"{seconds}s ago"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    return f"{seconds // 3600}h ago"


def provider_status_text(provider_id: str, *, app_settings: AppSettings, provider_policy: ProviderPolicy | None) -> str:
    catalog = get_catalog_status(provider_id, app_settings=app_settings)
    lines = [f"Models: {catalog.configured} configured + {catalog.discovered} discovered"]
    if catalog.refreshed_at is not None or catalog.stale:
        lines.append(
            f"Catalog: {observation_age(catalog.refreshed_at)}" + (" (stale, not unavailable)" if catalog.stale else "")
        )
    if catalog.last_error:
        lines.append(f"Last refresh: {catalog.last_error.replace('_', ' ')}")
    if provider_policy is None:
        return "Runtime: Unknown\n" + "\n".join(lines)
    state = provider_policy.snapshot(provider_id)
    lines.insert(0, f"Runtime: {state.state.replace('_', ' ').title()}")
    remaining = max(0, math.ceil(state.cooldown_until - time.time()))
    if remaining:
        lines.append(f"Retry in: {remaining}s")
    lines.append(f"Last success: {observation_age(state.last_success_at)}")
    if state.last_category:
        http = f" (HTTP {state.last_status})" if state.last_status is not None else ""
        lines.append(f"Last error: {state.last_category.replace('_', ' ')}{http}")
    if state.consecutive_failures:
        lines.append(f"Consecutive failures: {state.consecutive_failures}")
    raw_spec = load_routing_catalog(app_settings=app_settings).get(provider_id)
    spec = raw_spec if isinstance(raw_spec, Mapping) else {}
    unavailable = [
        m
        for m in model_ids(spec.get("models"))
        if provider_policy.snapshot(provider_id, m).state == "model_unavailable"
    ]
    if unavailable:
        labels = ", ".join(m[:32] for m in unavailable[:3])
        lines.append(f"Unavailable models: {len(unavailable)} ({labels})")
    return "\n".join(lines)
