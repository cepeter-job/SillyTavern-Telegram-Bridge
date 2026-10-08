"""Authorized, bounded troubleshooting views; never a general log-download API."""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from typing import Any

from bridge.diagnostic_events import clean_fields
from bridge.diagnostic_reader import read_events
from bridge.miniapp_auth import MiniAppIdentity
from bridge.miniapp_context import session_scope
from bridge.miniapp_errors import MiniAppError
from bridge.miniapp_types import ApiRoute

_FILTER = re.compile(r"[A-Za-z0-9_./:@+\-]{1,160}\Z")
_ALLOWED = frozenset({"session_id", "request_id", "purpose", "level", "limit"})
_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})


def _filters(values: dict[str, Any]) -> dict[str, Any]:
    if set(values) - _ALLOWED:
        raise MiniAppError("Unsupported diagnostic filter.")
    result: dict[str, Any] = {}
    for key in ("request_id", "purpose"):
        value = values.get(key, "")
        if not isinstance(value, str) or (value and not _FILTER.fullmatch(value)):
            raise MiniAppError("Invalid diagnostic filter.")
        result[key] = value
    level = values.get("level", "DEBUG")
    if not isinstance(level, str) or level not in _LEVELS:
        raise MiniAppError("Invalid diagnostic level.")
    limit = values.get("limit", 200)
    if type(limit) is int:
        count = limit
    elif isinstance(limit, str) and re.fullmatch(r"[0-9]{1,3}", limit):
        count = int(limit)
    else:
        raise MiniAppError("Invalid diagnostic limit.")
    if not 1 <= count <= 500:
        raise MiniAppError("Diagnostic limit must be between 1 and 500.")
    if "session_id" in values:
        session_id = values["session_id"]
        if not isinstance(session_id, str) or not session_id or len(session_id) > 200 or "\x00" in session_id:
            raise MiniAppError("Invalid diagnostic session.")
    return {**result, "level": level, "limit": count}


def _runtime_metadata(services: Any) -> dict[str, Any]:
    """Only boot-observed version identity, not installed files or whole health snapshots."""
    deployment: dict[str, object] = {}
    health = getattr(services, "health", None)
    if health is not None:
        try:
            snapshot = health.snapshot()
            identity = snapshot.get("deployment", {})
            if isinstance(identity, dict):
                deployment = clean_fields({key: identity.get(key) for key in ("version", "commit")})
        except Exception:
            pass
    return {
        "deployment": deployment,
        "log_level": logging.getLevelName(logging.getLogger().getEffectiveLevel()),
        "scope": "authorized_active_session",
    }


def diagnostic_timeline(services: Any, who: MiniAppIdentity, values: dict[str, Any]) -> dict[str, Any]:
    filters = _filters(values)
    with session_scope(services, who, values) as scope:
        active_id = str(scope.session["session_id"])
        if "session_id" in values and values["session_id"] != active_id:
            raise MiniAppError(
                "The active session changed. Refresh diagnostics before exporting.", status=409, code="stale"
            )
        # Never use a client-supplied owner, pathname, or pseudonym to authorize a read.
        result = read_events(
            services.config.log_file,
            chat_id=scope.chat_id,
            session_id=active_id,
            **filters,
        )
    result["runtime"] = _runtime_metadata(services)
    result["limitations"] = (
        "A bounded retained-log window, not a complete audit trail. Legacy free-form logs and other sessions are excluded. "
        "Rotation, log levels, filters, unreadable files and credential rotation can omit events. "
        "Missing usage is unknown, not zero; reported totals include failed and fallback attempts in this window."
    )
    return result


def diagnostic_export(services: Any, who: MiniAppIdentity, values: dict[str, Any]) -> dict[str, Any]:
    """Export the same revalidated view; no raw logs, configuration, database or story content."""
    return {
        "schema": 1,
        "kind": "sillytavern-diagnostics",
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        **diagnostic_timeline(services, who, values),
    }


def routes() -> list[ApiRoute]:
    return [
        ApiRoute("GET", "/diagnostics", diagnostic_timeline),
        ApiRoute("GET", "/diagnostics/export", diagnostic_export),
    ]
