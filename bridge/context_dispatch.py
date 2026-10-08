"""Ephemeral construction accounting and pre-dispatch context preparation."""

from __future__ import annotations

import math

from bridge.context_compaction import context_profile
from bridge.context_section_metrics import SECTION_CATEGORIES
from bridge.context_selection_runtime import ContextSelectionStaleError, choose_context_messages
from bridge.settings import AppSettings


def _text_size(message: dict) -> int:
    content = message.get("content", "")
    if isinstance(content, str):
        return len(content)
    if isinstance(content, list):
        return sum(
            len(item["text"]) for item in content if isinstance(item, dict) and isinstance(item.get("text"), str)
        )
    return 0


def capture_prompt_sections(messages: list[dict], *, scene_chars: int, world_chars: int) -> None:
    """Capture only counts using the builder's own boundaries, never headings."""
    counts = dict.fromkeys(SECTION_CATEGORIES, 0)
    for index, message in enumerate(messages):
        size = _text_size(message)
        derived = sum(span["end"] - span["start"] for span in message.get("_context_optional", ()))
        counts["derived"] += derived
        if index == 0:
            counts["derived"] += scene_chars
            counts["world_info"] = world_chars
            counts["mandatory"] += size - derived - scene_chars - world_chars
        elif index == len(messages) - 1:
            counts["task"] += size - derived
        else:
            category = "history" if message.get("role") in {"user", "assistant"} else "mandatory"
            counts[category] += size - derived
    if messages:
        messages[0]["_context_section_chars"] = counts


def prepare_context_dispatch(
    messages: list[dict],
    *,
    app_settings: AppSettings,
    model: str = "",
    requested_output_tokens: int | None = None,
) -> tuple[list[dict], dict[str, object]]:
    """Extract construction metrics before the ordinary final budget check."""
    result = [dict(message) for message in messages]
    stats: dict[str, object] = {}
    profile = context_profile(model, app_settings=app_settings, requested_output_tokens=requested_output_tokens)
    ratio = profile.chars_per_token
    counts = result[0].get("_context_section_chars") if result else None
    if isinstance(counts, dict) and all(
        isinstance(counts.get(key), int) and not isinstance(counts[key], bool) and counts[key] >= 0
        for key in SECTION_CATEGORIES
    ):
        counts = {key: counts[key] for key in SECTION_CATEGORIES}
        # Novel and action contracts are appended after the base builder.
        counts["task"] += max(0, sum(_text_size(message) for message in result) - sum(counts.values()))
        stats["section_estimated_tokens"] = {key: math.ceil(value / ratio) for key, value in counts.items()}
    for message in result:
        message.pop("_context_section_chars", None)
    try:
        result, selection_metrics = choose_context_messages(
            result,
            app_settings=app_settings,
            chars_per_token=ratio,
            input_budget_tokens=profile.input_budget_tokens,
        )
    except ContextSelectionStaleError as exc:
        exc.stats.update(stats)
        raise
    stats["selection_metrics"] = selection_metrics
    return result, stats
