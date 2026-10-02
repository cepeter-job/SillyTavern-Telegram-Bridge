"""Serialized recovery callbacks for repeated Light Novel protocol failures."""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable

from bridge.composition import BridgeServices
from bridge.conversation_lifecycle import conversation_state
from bridge.failed_turns import latest_failed_turn
from bridge.job_service import JobSubmission
from bridge.model_selection import task_model_for_session
from bridge.panel_bindings import discard_panel_session

_PROTOCOL_ERRORS = {
    "Story response has no usable narrative",
    "Malformed story envelope; no complete narrative to recover",
}
_SYNTHETIC_UPDATE_NAMESPACE = 1_000_000_000_000


def _generation_update_id(operation_id: int | None) -> int:
    if operation_id is None:
        raise ValueError("Retry panel expired")
    return -(_SYNTHETIC_UPDATE_NAMESPACE + int(operation_id))


def _close_panel(
    services: BridgeServices,
    db: sqlite3.Connection,
    chat_id: str,
    message_id: int,
    text: str,
) -> None:
    try:
        services.telegram.request(
            services.config.bot_token,
            "editMessageText",
            {
                "chat_id": chat_id,
                "message_id": message_id,
                "text": text,
                "reply_markup": {"inline_keyboard": []},
            },
        )
    except Exception:
        logging.info("Retry recovery panel cleanup unavailable")
    finally:
        discard_panel_session(db, chat_id, message_id)


def handle_failed_turn_retry_callback(
    services: BridgeServices,
    db: sqlite3.Connection,
    callback: dict,
    chat_id: str,
    session: dict,
    operation_id: int | None,
    actor_id: str,
    *,
    message_worker: Callable[..., None] | None,
) -> bool:
    data = str(callback.get("data") or "")
    if not data.startswith("lnturnretry:"):
        return False

    message_id = int((callback.get("message") or {}).get("message_id") or 0)
    try:
        parts = data.split(":")
        if len(parts) != 3 or len(data.encode()) > 64:
            raise ValueError("Retry panel expired")
        failed_message_id = int(parts[1])
        route = parts[2]
        failed = latest_failed_turn(db, chat_id)
        if (
            failed is None
            or int(failed[0]) != failed_message_id
            or int(failed[3] or 0) < 2
            or str(failed[4] or "") not in _PROTOCOL_ERRORS
        ):
            raise ValueError("Retry panel expired")
        failed_session_id = str(failed[5] or "")
        if not failed_session_id or failed_session_id != str(session.get("session_id") or ""):
            raise ValueError("Retry panel expired")
        state = conversation_state(db, chat_id, failed_session_id)
        if not state.started or state.mode != "lightnovel":
            raise ValueError("Retry panel expired")
        if route == "close":
            _close_panel(services, db, chat_id, message_id, "Retry panel closed.")
            return True
        if route not in {"same", "current", "utility", "b", "c"} or message_worker is None:
            raise ValueError("Retry panel expired")

        current_model = str(session.get("model_id") or services.config.default_model)
        if route == "same":
            retry_model, retry_strategy = str(failed[2] or "") or current_model, "a"
        elif route == "current":
            retry_model, retry_strategy = current_model, "a"
        elif route == "utility":
            retry_model = task_model_for_session(db, chat_id, session, "utility", app_settings=services.config)
            retry_strategy = "a"
        else:
            retry_model, retry_strategy = current_model, route
        payload = {
            "text": str(failed[1]),
            "model": retry_model,
            "story_model_override": retry_model,
            "light_novel_strategy_override": retry_strategy,
            "actor_id": actor_id,
            "resolve_active": False,
            "epoch": state.epoch,
        }
        job_id = services.jobs.enqueue(
            db,
            _generation_update_id(operation_id),
            chat_id,
            failed_session_id,
            failed_message_id,
            "generation",
            payload,
        )
        services.jobs.submit(
            db,
            job_id,
            JobSubmission(
                "generation",
                chat_id,
                message_worker,
                (
                    services,
                    {},
                    chat_id,
                    str(failed[1]),
                    failed_message_id,
                    None,
                    failed_session_id,
                    retry_model,
                ),
            ),
        )
        label = f"Strategy {retry_strategy.upper()}" if retry_strategy != "a" else "Strategy A"
        _close_panel(services, db, chat_id, message_id, f"Retry queued · {label}")
    except (ValueError, TypeError, IndexError):
        _close_panel(services, db, chat_id, message_id, "Retry panel expired. Run /retry again.")
    return True
