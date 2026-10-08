"""Persisted, redacted context-window diagnostics for UI surfaces."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from bridge.context_compaction import context_profile
from bridge.context_section_metrics import BUILDER_METRIC_FIELDS, redact_builder_metrics
from bridge.metadata import get_meta, set_meta
from bridge.settings import AppSettings

_INT_FIELDS = {
    "requested_output_tokens",
    "window_tokens",
    "output_reserve_tokens",
    "safety_margin_tokens",
    "budget_tokens",
    "input_cap_tokens",
    "original_tokens",
    "final_tokens",
    "dropped_history",
}
_BOOL_FIELDS = {
    "rag_trimmed",
    "memory_trimmed",
    "npc_trimmed",
    "summary_trimmed",
    "over_budget",
    "output_reservation_only",
    "estimated",
    "allocation_invalid",
}
_ENUM_FIELDS = {
    "source": {"global-fallback", "provider", "provider-model", "discovered-provider-model", "codex-alias"},
    "input_budget_limiter": {"input-cap", "model-window"},
}


def _redact_context_stats(stats: dict[str, object]) -> dict[str, object]:
    redacted = redact_builder_metrics(stats)
    for key in _INT_FIELDS:
        value = stats.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            redacted[key] = value
    for key in _BOOL_FIELDS:
        if isinstance(stats.get(key), bool):
            redacted[key] = stats[key]
    value = stats.get("chars_per_token")
    if isinstance(value, (int, float)) and not isinstance(value, bool) and 1.0 <= value <= 8.0:
        redacted["chars_per_token"] = float(value)
    for key, choices in _ENUM_FIELDS.items():
        value = stats.get(key)
        if isinstance(value, str) and value in choices:
            redacted[key] = value
    for key in ("model", "request_stage"):
        value = stats.get(key)
        if isinstance(value, str):
            redacted[key] = value[:160]
    return redacted


def context_stats_key(chat_id: str, session_id: str) -> str:
    return f"context_stats:{chat_id}:{session_id}"


def save_context_stats(db: sqlite3.Connection, chat_id: str, session_id: str, stats: dict[str, object]) -> None:
    """Persist only numbers, flags and known metadata; never prompt content."""
    redacted = _redact_context_stats(stats)
    set_meta(db, context_stats_key(chat_id, session_id), json.dumps(redacted, sort_keys=True))


@contextmanager
def record_context_attempts(
    db: sqlite3.Connection, chat_id: str, session_id: str
) -> Iterator[Callable[[dict[str, object]], None]]:
    """Collect transport observations and persist on the application thread."""
    attempts: list[dict[str, object]] = []
    try:
        yield attempts.append
    finally:
        if attempts:
            raw = get_meta(db, context_stats_key(chat_id, session_id), "")
            try:
                previous = json.loads(raw) if raw else {}
            except (TypeError, ValueError):
                previous = {}
            previous = _redact_context_stats(previous) if isinstance(previous, dict) else {}
            stats = {
                key: value for key, value in previous.items() if key == "dropped_history" or key.endswith("_trimmed")
            }
            stats.update({key: value for key, value in attempts[-1].items() if key not in BUILDER_METRIC_FIELDS})
            stats.update({key: value for key, value in previous.items() if key in BUILDER_METRIC_FIELDS})
            save_context_stats(db, chat_id, session_id, stats)


def context_diagnostics_snapshot(
    db: sqlite3.Connection,
    chat_id: str,
    session: dict[str, str],
    *,
    app_settings: AppSettings,
) -> dict[str, object]:
    profile = context_profile(str(session.get("model_id") or ""), app_settings=app_settings)
    result: dict[str, object] = {
        "window_tokens": profile.window_tokens,
        "output_reserve_tokens": profile.output_reserve_tokens,
        "safety_margin_tokens": profile.safety_margin_tokens,
        "budget_tokens": profile.input_budget_tokens,
        "configured_input_cap_tokens": profile.input_cap_tokens,
        "input_cap_tokens": profile.input_cap_tokens,
        "input_budget_limiter": profile.input_budget_limiter,
        "chars_per_token": profile.chars_per_token,
        "source": profile.source,
        "original_tokens": None,
        "final_tokens": None,
        "dropped_history": 0,
        "rag_trimmed": False,
        "memory_trimmed": False,
        "npc_trimmed": False,
        "summary_trimmed": False,
        "over_budget": False,
    }
    raw = get_meta(db, context_stats_key(chat_id, str(session["session_id"])), "")
    try:
        saved = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        saved = {}
    if not isinstance(saved, dict):
        return result
    result.update(_redact_context_stats(saved))
    final_tokens = result.get("final_tokens")
    budget_tokens = result.get("budget_tokens")
    result["usage_percent"] = (
        round(int(final_tokens) * 100 / int(budget_tokens))
        if isinstance(final_tokens, int) and isinstance(budget_tokens, int) and budget_tokens > 0
        else None
    )
    trimmed_components = [name for name in ("memory", "rag", "npc", "summary") if result.get(f"{name}_trimmed") is True]
    result["trimmed_components"] = trimmed_components
    dropped_history = result.get("dropped_history")
    result["compacted"] = bool((dropped_history if isinstance(dropped_history, int) else 0) or trimmed_components)
    return result
