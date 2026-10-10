"""Content-free failure diagnostics for Hindsight retention."""

from __future__ import annotations

import json
import logging

from bridge.diagnostic_events import event


def hindsight_retain_failure_reason(error: BaseException) -> str:
    """Classify a retain failure without persisting response text or credentials."""
    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, json.JSONDecodeError):
            return "upstream_invalid_response"
        response = getattr(current, "response", None)
        if isinstance(getattr(response, "status_code", None), int):
            return "upstream_http_error"
        if isinstance(current, TimeoutError) or "timeout" in type(current).__name__.casefold():
            return "upstream_timeout"
        current = current.__cause__ or current.__context__
    return "retain_failed"


def handle_hindsight_retain_failure(error: BaseException, chat_id: str, session_id: str) -> bool:
    """Record a bounded classification and preserve the boolean retain contract."""
    event(
        "memory.hindsight_retain_failed",
        chat_id=chat_id,
        session_id=session_id,
        reason=hindsight_retain_failure_reason(error),
        error_type=type(error).__name__,
        retryable=True,
    )
    logging.warning("Hindsight native fact retain unavailable for chat %s", chat_id, exc_info=True)
    return False
