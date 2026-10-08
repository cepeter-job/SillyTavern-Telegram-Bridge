"""Lossless, local formatting projection for the classified summary accumulator."""

from __future__ import annotations

import json
import logging
from typing import Any


def project_summary_messages(messages: list[dict], previous: dict[str, Any], *, mode: str) -> list[dict]:
    """Compact JSON whitespace only; preserve the complete source and request fields.

    This consumes the existing durable processor's accumulator and never stores
    or retrieves state. Shadow evaluates a candidate locally and returns the
    original request. Unexpected input fails open to the original request.
    """
    if mode not in {"shadow", "enabled"}:
        return messages
    try:
        if not messages or messages[-1].get("role") != "user":
            return messages
        content = messages[-1].get("content")
        if not isinstance(content, str):
            return messages
        serialized = json.dumps(previous, ensure_ascii=False, allow_nan=False)
        prefix = "Previous classified summary:\n" + serialized
        if not content.startswith(prefix + "\nSource role: "):
            return messages
        compact = json.dumps(previous, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        if len(compact) >= len(serialized) or json.loads(compact) != json.loads(serialized):
            return messages
        candidate = [dict(message) for message in messages]
        candidate[-1]["content"] = "Previous classified summary:\n" + compact + content[len(prefix) :]
    except (TypeError, ValueError, RecursionError):
        return messages
    logging.info("summary_input_projection mode=%s saved_chars=%s", mode, len(serialized) - len(compact))
    return candidate if mode == "enabled" else messages
