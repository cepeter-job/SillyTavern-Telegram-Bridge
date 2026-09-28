"""Redacted runtime status and actor-confirmed access to the existing verified updater."""

from __future__ import annotations

import json
import secrets
import sqlite3
import time
from typing import Any

from bridge.metadata import get_meta, set_meta
from bridge.miniapp_auth import MiniAppIdentity
from bridge.miniapp_context import require_confirmation, session_scope, text
from bridge.miniapp_errors import MiniAppError
from bridge.miniapp_status_repository import counts
from bridge.miniapp_types import ApiRoute
from bridge.self_update import UpdateStatus, version_tuple
from bridge.sqlite_store import write_transaction
from bridge.update import _run_update, format_update_outcome, installed_bridge_version, latest_bridge_release
from bridge.update_ack import arm_pending_update_ack


def system_status(services: Any, who: MiniAppIdentity, values: dict) -> dict:
    health = getattr(services, "health", None)
    data = (
        health.snapshot()
        if health is not None
        else {
            "deployment": {"version": "unknown", "commit": ""},
            "telegram": {"state": "unobserved", "last_success": 0},
            "uptime_seconds": 0,
            "started_at": 0,
        }
    )
    with session_scope(services, who, values) as scope:
        data.update(
            {
                "database": {
                    **counts(scope.db, scope.chat_id),
                    "sqlite_version": sqlite3.sqlite_version,
                    "state": "query_ok",
                },
                "session": scope.session,
                "installed_version": installed_bridge_version(app_settings=services.config),
                "automatic_update_trust_configured": bool(
                    services.config.update_allowed_signers and services.config.update_allowed_signers.is_file()
                ),
            }
        )
        return data


def review_update(services: Any, who: MiniAppIdentity, values: dict) -> dict:
    try:
        version, notes = latest_bridge_release()
        installed = installed_bridge_version(app_settings=services.config)
        update_available = version_tuple(version) > version_tuple(installed)
    except Exception:
        raise MiniAppError("Release metadata could not be checked. Try again later.", status=503) from None

    if update_available:
        status = "update_available"
    elif version == installed:
        status = "already_latest"
    else:
        status = "local_newer"

    key = f"miniapp_update_confirmation:{who.user_id}"
    result = {
        "installed": installed,
        "latest": version,
        "notes": notes,
        "update_available": update_available,
        "status": status,
    }
    with session_scope(services, who, values) as scope:
        if not update_available:
            set_meta(scope.db, key, "")
            return result
        nonce = secrets.token_hex(24)
        set_meta(
            scope.db,
            key,
            json.dumps({"nonce": nonce, "version": version, "expires": time.time() + 300}),
        )
    result.update({"confirmation": nonce, "expires_in": 300})
    return result


def perform_update(services: Any, who: MiniAppIdentity, values: dict) -> dict:
    require_confirmation(values)
    nonce = text(values, "confirmation", 48)
    version = text(values, "version", 24)
    with session_scope(services, who, values, write=True) as scope:
        key = f"miniapp_update_confirmation:{who.user_id}"
        with write_transaction(scope.db):
            try:
                state = json.loads(get_meta(scope.db, key, "{}"))
                valid = (
                    state.get("nonce") == nonce
                    and state.get("version") == version
                    and state.get("expires", 0) >= time.time()
                )
            except (ValueError, TypeError, AttributeError):
                valid = False
            if not valid:
                raise MiniAppError("Update review expired or changed. Review the release again.", status=409)
            set_meta(scope.db, key, "")
        outcome = _run_update(
            expected_version=version,
            app_settings=services.config,
            before_restart=lambda installed: arm_pending_update_ack(
                who.chat_id, installed.version, commit=installed.commit, app_settings=services.config
            ),
        )
        # Restart scheduling is not proof the replacement process started.
        result = {
            "status": outcome.status.value,
            "version": outcome.version,
            "commit": outcome.commit,
            "message": format_update_outcome(outcome),
        }
        if outcome.status is UpdateStatus.ALREADY_LATEST:
            raise MiniAppError(result["message"], status=409, code="already_latest")
        if outcome.status is not UpdateStatus.RESTART_SCHEDULED:
            raise MiniAppError(result["message"], status=409)
        return result


def routes() -> list[ApiRoute]:
    return [
        ApiRoute("GET", "/status", system_status),
        ApiRoute("GET", "/update", review_update),
        ApiRoute("POST", "/update", perform_update, "verified_update"),
    ]
