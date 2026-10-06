"""Read-only token usage text; no Mini App or inline panel is opened."""

from __future__ import annotations

import sqlite3
import time
from typing import Any

from bridge.delivery_port import DeliveryPort
from bridge.extension_registry import register_command_route
from bridge.provider_port import ProviderPort
from bridge.request_types import RequestContext
from bridge.token_usage_repository import usage_totals


def _count(value: int | None) -> str:
    return "unknown" if value is None else f"{value:,}"


def _label(value: object, limit: int = 80) -> str:
    text = " ".join(str(value).split())
    return text if len(text) <= limit else text[:limit] + "…"


def usage_text(db: sqlite3.Connection, chat_id: str, session_id: str, session: dict[str, Any]) -> str:
    now = time.time()
    usage = usage_totals(db, chat_id, session_id, now - 7 * 86400, now)
    title = _label(session.get("title") or session_id)
    totals = usage["totals"]
    calls = totals["calls"]
    lines = ["📊 Token usage — last 7 days", f"Session: {title} ({_label(session_id)})"]
    if not calls:
        lines.append("No provider calls recorded for this session in the last 7 days.")
    else:
        lines.extend(
            [
                f"Calls: {calls:,} · Reported: {totals['reported_calls']:,}/{calls:,} "
                f"· Complete: {totals['complete_calls']:,}/{calls:,}",
                f"Failed: {totals['failed_calls']:,} · Cancelled: {totals['cancelled_calls']:,}",
                f"Input: {_count(totals['input_tokens'])}\n"
                f"Output: {_count(totals['output_tokens'])}\n"
                f"Total: {_count(totals['total_tokens'])}\n"
                f"Cached: {_count(totals['cached_tokens'])}\n"
                f"Reasoning: {_count(totals['reasoning_tokens'])}",
                "Cached and reasoning tokens are subsets, not added to Total.",
            ]
        )
        for heading, key, label in (
            ("Daily (UTC)", "daily", "day"),
            ("Top models (up to 5)", "models", "model"),
            ("Top purposes (up to 5)", "purposes", "purpose"),
        ):
            rows = usage[key] if key == "daily" else usage[key][:5]
            lines.append(
                heading
                + "\n"
                + "\n".join(
                    f"• {_label(row[label])}: {_count(row['total_tokens'])} tokens ({row['calls']:,} calls)"
                    for row in rows
                )
            )
    if calls and totals["complete_calls"] < calls:
        lines.append("Some calls have missing or partial usage; totals may be incomplete.")
    lines.append("Provider-reported tokens only; not a billing statement.")
    return "\n\n".join(lines)


def _usage_command_route(
    db: sqlite3.Connection,
    token: str,
    api_key: str,
    model: str,
    fields: dict[str, Any],
    chat_id: str,
    stripped: str,
    command: str,
    session: dict[str, Any],
    session_id: str,
    current_model: str,
    current_persona: str,
    user_name: str,
    operation_id: int | str | None = None,
    *,
    request_context: RequestContext,
    delivery_port: DeliveryPort,
    provider_port: ProviderPort,
) -> bool:
    if command != "/usage" and not command.startswith("/usage "):
        return False
    delivery_port.send_text(token, chat_id, usage_text(db, chat_id, session_id, session))
    return True


def register_usage_extensions() -> None:
    register_command_route("usage", _usage_command_route)
