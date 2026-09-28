"""Read-only token reporting scoped to the authenticated private chat."""

from __future__ import annotations

import time
from typing import Any

from bridge.miniapp_auth import MiniAppIdentity
from bridge.miniapp_context import session_scope
from bridge.miniapp_errors import MiniAppError
from bridge.miniapp_types import ApiRoute
from bridge.token_usage import RETENTION_DAYS
from bridge.token_usage_repository import usage_totals


def usage_summary(services: Any, who: MiniAppIdentity, values: dict) -> dict:
    period = values.get("period", "7d")
    scope = values.get("scope", "session")
    if period not in ("1d", "7d", "30d") or scope not in ("session", "all"):
        raise MiniAppError("Choose a supported usage period and scope.")
    now = time.time()
    with session_scope(services, who, values) as context:
        sid = context.session["session_id"] if scope == "session" else None
        result = usage_totals(context.db, context.chat_id, sid, now - int(period[:-1]) * 86400, now)
        return {
            **result,
            "session_id": context.session["session_id"],
            "period": period,
            "scope": scope,
            "retention_days": RETENTION_DAYS,
            "timezone": "UTC",
            "as_of": now,
            "measurement": "provider_reported",
            "history": "since_tracking_enabled",
        }


def routes() -> list[ApiRoute]:
    return [ApiRoute("GET", "/usage", usage_summary)]
