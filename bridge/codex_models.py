"""Curated OpenAI Codex picker aliases and context limits."""

from __future__ import annotations

CODEX_LARGE_CONTEXT_WINDOW_TOKENS = 900_000

_LARGE_CONTEXT_ALIASES = {f"gpt-5.6-{family}-900k": f"gpt-5.6-{family}" for family in ("sol", "terra", "luna")}


def codex_wire_model(model: str) -> str:
    """Return the Codex API slug for a verified picker-only alias."""
    normalized = str(model or "").strip().lower()
    return _LARGE_CONTEXT_ALIASES.get(normalized, model)


def codex_context_window_tokens(model_selection: str) -> int | None:
    """Return the opt-in context window for a provider-qualified alias."""
    provider, separator, model = str(model_selection or "").strip().lower().partition("::")
    if separator and provider == "openai-codex" and model in _LARGE_CONTEXT_ALIASES:
        return CODEX_LARGE_CONTEXT_WINDOW_TOKENS
    return None
