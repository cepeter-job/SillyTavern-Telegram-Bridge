"""Allowlisted completion metadata; token counts never imply truncation."""

from __future__ import annotations

from bridge.diagnostic_events import event

_CAPPED = frozenset({"length", "max_tokens", "max_output_tokens"})
_NOT_CAPPED = frozenset(
    {
        "stop",
        "end_turn",
        "stop_sequence",
        "tool_use",
        "tool_calls",
        "function_call",
        "content_filter",
        "refusal",
        "pause_turn",
    }
)


def report_finish_reason(reason: object) -> None:
    fields: dict[str, object] = {}
    if reason is not None:
        known = isinstance(reason, str) and reason in _CAPPED | _NOT_CAPPED
        fields["finish_reason"] = reason if known else "unknown"
        if known:
            fields["output_cap_reached"] = reason in _CAPPED
    event("provider.output_finished", **fields)


def report_response_completion(payload: object) -> None:
    """Responses APIs expose an incomplete reason, not a Chat Completions finish_reason."""
    if not isinstance(payload, dict):
        return
    response = payload.get("response", payload)
    if not isinstance(response, dict):
        return
    details = response.get("incomplete_details")
    if isinstance(details, dict) and "reason" in details:
        report_finish_reason(details["reason"])
