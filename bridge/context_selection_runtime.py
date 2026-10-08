"""Apply canonical candidates only to construction-owned optional payload spans."""

from __future__ import annotations

import sqlite3

from bridge.context_compaction import estimate_message_tokens
from bridge.context_selection import (
    CONTEXT_SELECTION_REASONS,
    context_selection_mode,
    context_slice_enabled,
)
from bridge.limits import EPISODIC_CONTEXT_MAX_CHARS, HINDSIGHT_CONTEXT_MAX_CHARS
from bridge.settings import AppSettings

_PAYLOADS = {
    "memory": ("recall", HINDSIGHT_CONTEXT_MAX_CHARS),
    "episodic": ("episodic", EPISODIC_CONTEXT_MAX_CHARS),
}


class ContextSelectionStaleError(ValueError):
    """A captured source or reader was revoked before the provider request."""

    def __init__(self, metrics: dict[str, object]):
        super().__init__("Story changed while preparing context. Please retry.")
        self.stats: dict[str, object] = {"selection_metrics": metrics}


def _text(message: dict) -> str | None:
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts = [part for part in content if isinstance(part, dict) and part.get("type") == "text"]
        if len(texts) == 1 and isinstance(texts[0].get("text"), str):
            return texts[0]["text"]
    return None


def _replace_text(message: dict, value: str) -> None:
    if isinstance(message.get("content"), str):
        message["content"] = value
    else:
        message["content"] = [
            dict(part, text=value) if isinstance(part, dict) and part.get("type") == "text" else part
            for part in message["content"]
        ]


def _candidate_messages(messages: list[dict], context: object) -> list[dict] | None:
    selection = getattr(context, "selection", None)
    if selection is None:
        return None
    baseline_blocks = getattr(context, "baseline_blocks", ())
    if len(baseline_blocks) != len(selection.blocks):
        return None
    replacements = {}
    for baseline, candidate in zip(baseline_blocks, selection.blocks, strict=True):
        if baseline.channel != candidate.channel:
            return None
        for kind, (channel, maximum) in _PAYLOADS.items():
            if baseline.channel == channel and max(len(baseline.text), len(candidate.text)) > maximum:
                return None
            if baseline.channel == channel and baseline.text != candidate.text:
                if getattr(context, channel, None) != baseline.text or len(candidate.text) > len(baseline.text):
                    return None
                replacements[kind] = (baseline.text[:maximum], candidate.text[:maximum])
    if not replacements:
        return messages
    result = []
    matched: set[str] = set()
    for message in messages:
        updated = dict(message)
        spans = [dict(span) for span in message.get("_context_optional", ())]
        for span in sorted(spans, key=lambda span: span["start"], reverse=True):
            kind = span["kind"]
            if kind not in replacements:
                continue
            original, candidate = replacements[kind]
            text = _text(updated)
            start, end = span["start"], span["end"]
            if kind in matched or text is None or text[start:end] != original:
                return None
            matched.add(kind)
            _replace_text(updated, text[:start] + candidate + text[end:])
            difference = len(candidate) - (end - start)
            for other in spans:
                if other is span:
                    other["end"] += difference
                elif other["start"] >= end:
                    other["start"] += difference
                    other["end"] += difference
        if "_context_optional" in updated:
            updated["_context_optional"] = spans
        result.append(updated)
    return result if matched == replacements.keys() else None


def choose_context_messages(
    messages: list[dict], *, app_settings: AppSettings, chars_per_token: float, input_budget_tokens: int
) -> tuple[list[dict], dict[str, object]]:
    """Select once; shadow preserves valid baseline bytes, revoked captures stop."""
    baseline = [dict(message) for message in messages]
    context = baseline[0].get("_context_selection") if baseline else None
    for message in baseline:
        message.pop("_context_selection", None)
    mode = context_selection_mode(app_settings)
    original = estimate_message_tokens(baseline, chars_per_token=chars_per_token)
    metrics: dict[str, object] = {
        "mode": mode,
        "reason": "off",
        "original_tokens": original,
        "candidate_tokens": original,
        "selected_blocks": 0,
        "deduplicated_blocks": 0,
        "coverage_valid": False,
        "applied": False,
    }
    metrics["coverage_valid"] = getattr(context, "selection_coverage_valid", False) is True
    if mode == "off":
        return baseline, metrics
    reason = getattr(context, "selection_reason", "ambiguous")
    metrics["reason"] = reason if reason in CONTEXT_SELECTION_REASONS else "ambiguous"
    guard = getattr(context, "selection_guard", None)
    if callable(guard):
        try:
            failure = guard()
        except (ValueError, RuntimeError, sqlite3.Error):
            failure = "ambiguous"
        if failure:
            metrics["coverage_valid"] = False
            metrics["reason"] = failure if failure in CONTEXT_SELECTION_REASONS else "ambiguous"
            if failure in {"source_changed", "invalid_scope"}:
                # The old baseline contains the same revoked evidence. Returning
                # it is not a safe optimization fallback; the caller must retry.
                raise ContextSelectionStaleError(metrics)
            return baseline, metrics
    if mode == "enabled" and not context_slice_enabled(app_settings, "dedup"):
        if reason in {"off", "selected", "no_savings", "ambiguous"}:
            metrics["reason"] = "not_approved"
        return baseline, metrics
    if reason not in {"selected", "no_savings"} or getattr(context, "selection_mode", None) != mode:
        return baseline, metrics
    if not callable(guard):
        metrics["reason"] = "invalid_scope"
        return baseline, metrics
    selection = getattr(context, "selection", None)
    candidate = _candidate_messages(baseline, context)
    if candidate is None or selection is None:
        metrics["reason"] = "ambiguous"
        return baseline, metrics
    candidate_tokens = estimate_message_tokens(candidate, chars_per_token=chars_per_token)
    metrics.update(
        {
            "selected_blocks": selection.selected_blocks,
            "deduplicated_blocks": selection.deduplicated_blocks,
            "candidate_tokens": candidate_tokens,
        }
    )
    if candidate_tokens >= original:
        metrics["reason"] = "no_savings"
        return baseline, metrics
    # Later optional compaction could remove the representative whose duplicate
    # was discarded. Only activate a candidate that keeps its full source text.
    if candidate_tokens > input_budget_tokens:
        metrics["reason"] = "ambiguous"
        return baseline, metrics
    metrics["reason"] = "selected"
    metrics["applied"] = mode == "enabled"
    return (candidate if mode == "enabled" else baseline), metrics
