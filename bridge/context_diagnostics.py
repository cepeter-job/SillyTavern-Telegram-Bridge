"""Persisted, redacted context-window diagnostics for UI surfaces."""

from __future__ import annotations

import json
import sqlite3

from bridge.context_compaction import context_profile
from bridge.metadata import get_meta
from bridge.settings import AppSettings

_INT_FIELDS = {
    "window_tokens",
    "output_reserve_tokens",
    "safety_margin_tokens",
    "budget_tokens",
    "original_tokens",
    "final_tokens",
    "dropped_history",
}
_BOOL_FIELDS = {"rag_trimmed", "memory_trimmed", "npc_trimmed", "summary_trimmed", "over_budget"}


def context_stats_key(chat_id: str, session_id: str) -> str:
    return f"context_stats:{chat_id}:{session_id}"


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
    for key in _INT_FIELDS:
        value = saved.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            result[key] = value
    value = saved.get("chars_per_token")
    if isinstance(value, (int, float)) and not isinstance(value, bool) and 1.0 <= float(value) <= 8.0:
        result["chars_per_token"] = float(value)
    source = saved.get("source")
    if isinstance(source, str) and source in {
        "global-fallback",
        "provider",
        "provider-model",
        "discovered-provider-model",
        "codex-alias",
    }:
        result["source"] = source
    for key in _BOOL_FIELDS:
        if isinstance(saved.get(key), bool):
            result[key] = saved[key]
    final_tokens = result.get("final_tokens")
    budget_tokens = result.get("budget_tokens")
    result["usage_percent"] = (
        round(int(final_tokens) * 100 / int(budget_tokens))
        if isinstance(final_tokens, int) and isinstance(budget_tokens, int) and budget_tokens > 0
        else None
    )
    trimmed_components = [name for name in ("memory", "rag", "npc", "summary") if result.get(f"{name}_trimmed") is True]
    result["trimmed_components"] = trimmed_components
    result["compacted"] = bool(int(result.get("dropped_history") or 0) or trimmed_components)
    return result
