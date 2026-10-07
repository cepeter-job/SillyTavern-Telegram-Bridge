"""Callbacks for the inline action-check mode panel."""

from __future__ import annotations

from bridge.action_adjudication import set_action_mode
from bridge.callbacks import close_panel_message
from bridge.check_panels import send_check_menu, send_manual_check_help, send_recent_checks
from bridge.closed_session_guard import guard_story_mutation


def handle_check_panel_callback(
    db,
    token,
    callback,
    answer_callback,
    data,
    chat_id,
    message,
    session_id,
    *,
    request_context,
) -> bool:
    if not data.startswith("checkmode:"):
        return False
    action = data.split(":", 1)[1]
    callback_id = str(callback.get("id", ""))
    message_id = message.get("message_id")
    if action == "close":
        answer_callback(token, callback_id, "Closed")
        close_panel_message(db, token, chat_id, callback)
        return True
    if action in {"auto", "director", "manual"}:
        guard_story_mutation(db, chat_id, session_id)
        set_action_mode(db, chat_id, session_id, action)
        label = {"auto": "Auto", "director": "Director", "manual": "Manual"}[action]
        answer_callback(token, callback_id, f"{label} mode")
        send_check_menu(
            token,
            chat_id,
            db,
            session_id,
            message_id,
            request_context=request_context,
        )
        return True
    if action == "manual_help":
        answer_callback(token, callback_id, "Manual check")
        send_manual_check_help(token, chat_id, message_id, request_context=request_context)
        return True
    if action == "recent":
        answer_callback(token, callback_id, "Recent checks")
        send_recent_checks(
            token,
            chat_id,
            db,
            session_id,
            message_id,
            request_context=request_context,
        )
        return True
    if action == "back":
        answer_callback(token, callback_id, "Action checks")
        send_check_menu(
            token,
            chat_id,
            db,
            session_id,
            message_id,
            request_context=request_context,
        )
        return True
    answer_callback(token, callback_id, "Unknown check action")
    return True
