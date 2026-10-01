"""Actor/session-bound provider actions and cached manual-probe report handles."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass

from bridge.callback_tokens import dynamic_callback_token, resolve_dynamic_callback_token
from bridge.request_types import RequestContext


@dataclass(frozen=True)
class ProviderReport:
    checks: tuple[tuple[str, str, str], ...]
    checked_at: float
    provider_id: str | None = None


def _store(kind: str, value: dict, chat_id: str, *, request_context: RequestContext) -> str:
    payload = {**value, "actor_id": request_context.actor_id, "session_id": request_context.session_id}
    return dynamic_callback_token(kind, json.dumps(payload, separators=(",", ":")), chat_id, db=request_context.db)


def _load(token: str, kind: str, chat_id: str, *, request_context: RequestContext) -> dict | None:
    raw = resolve_dynamic_callback_token(token, kind, chat_id, db=request_context.db)
    if raw is None or len(raw) > 512_000:
        return None
    try:
        value = json.loads(raw)
    except ValueError:
        return None
    if (
        not isinstance(value, dict)
        or value.get("actor_id") != request_context.actor_id
        or value.get("session_id") != request_context.session_id
    ):
        return None
    return value


def provider_action_token(action: str, provider_id: str, chat_id: str, *, request_context: RequestContext) -> str:
    if action not in {"refresh", "test", "reset"}:
        raise ValueError("unknown provider action")
    return _store(
        "provider_action", {"action": action, "provider_id": provider_id}, chat_id, request_context=request_context
    )


def resolve_provider_action(token: str, action: str, chat_id: str, *, request_context: RequestContext) -> str | None:
    value = _load(token, "provider_action", chat_id, request_context=request_context)
    if value is None or value.get("action") != action:
        return None
    provider = value.get("provider_id")
    return provider if isinstance(provider, str) and 0 < len(provider) <= 200 else None


def store_provider_report(report: ProviderReport, chat_id: str, *, request_context: RequestContext) -> str:
    return _store(
        "provider_report",
        {
            "checks": report.checks,
            "checked_at": report.checked_at,
            "provider_id": report.provider_id,
        },
        chat_id,
        request_context=request_context,
    )


def load_provider_report(token: str, chat_id: str, *, request_context: RequestContext) -> ProviderReport | None:
    value = _load(token, "provider_report", chat_id, request_context=request_context)
    if value is None:
        return None
    rows = value.get("checks")
    stamp = value.get("checked_at")
    provider = value.get("provider_id")
    if (
        not isinstance(rows, list)
        or len(rows) > 1024
        or any(
            not isinstance(row, list) or len(row) != 3 or not all(isinstance(item, str) for item in row) for row in rows
        )
    ):
        return None
    if not isinstance(stamp, (float, int)) or isinstance(stamp, bool):
        return None
    try:
        checked_at = float(stamp)
    except OverflowError:
        return None
    if not math.isfinite(checked_at) or checked_at < 0 or (provider is not None and not isinstance(provider, str)):
        return None
    return ProviderReport(tuple((row[0], row[1], row[2]) for row in rows), checked_at, provider)
