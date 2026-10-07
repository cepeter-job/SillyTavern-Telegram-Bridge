"""Canonical session callbacks owner."""

from __future__ import annotations

from pathlib import Path

from bridge.callback_tokens import resolve_dynamic_callback_token
from bridge.callbacks import close_panel_message, remove_inline_keyboard
from bridge.cards import send_session_menu
from bridge.group_service import GroupService
from bridge.metadata import set_meta
from bridge.pending_input import pending_character_for_session
from bridge.session_core import delete_session_data, list_sessions, update_session
from bridge.session_naming import start_session_name_input
from bridge.session_panels import send_session_delete_confirm, send_session_delete_menu
from bridge.telegram import send_text


def _handle_protected(db, token, callback, answer_callback, chat_id, message, session_id, request_context):
    answer_callback(token, str(callback.get("id", "")), "Active session is protected")
    send_session_menu(
        token,
        chat_id,
        list_sessions(db, chat_id),
        session_id,
        message.get("message_id"),
        request_context=request_context,
    )
    return True


def _handle_delete_confirm(
    db, token, callback, answer_callback, data, chat_id, session_id, operation_id, memory_service, request_context
):
    target_session_id = resolve_dynamic_callback_token(data.split(":", 1)[1], "session", chat_id, db=db) or ""
    target = next((item for item in list_sessions(db, chat_id) if item["session_id"] == target_session_id), None)
    if target is None:
        answer_callback(token, str(callback.get("id", "")), "Session choice expired")
        return True
    deleted, reason = delete_session_data(
        db, chat_id, target_session_id, session_id, operation_id=operation_id, memory_service=memory_service
    )
    if not deleted:
        answer_callback(token, str(callback.get("id", "")), f"Deletion refused: {reason}")
        return True
    answer_callback(
        token,
        str(callback.get("id", "")),
        "Session deleted. Hindsight cleanup is queued and will retry in the background.",
    )
    remove_inline_keyboard(db, token, callback)
    send_session_menu(
        token,
        chat_id,
        list_sessions(db, chat_id),
        session_id,
        None,
        request_context=request_context,
    )
    return True


def _handle_delete(db, token, callback, answer_callback, data, chat_id, message, session_id, request_context):
    value = data.split(":", 1)[1]
    if value.startswith("page:"):
        answer_callback(token, str(callback.get("id", "")), "Page")
        send_session_delete_menu(
            token,
            chat_id,
            list_sessions(db, chat_id),
            session_id,
            message.get("message_id"),
            int(value.split(":", 1)[1]),
            request_context=request_context,
        )
        return True
    target_session_id = resolve_dynamic_callback_token(value, "session", chat_id, db=db) or ""
    target = next((item for item in list_sessions(db, chat_id) if item["session_id"] == target_session_id), None)
    if target is None or target_session_id == session_id:
        answer_callback(token, str(callback.get("id", "")), "Only an inactive session can be deleted")
        return True
    answer_callback(token, str(callback.get("id", "")), "Confirm deletion")
    send_session_delete_confirm(
        token,
        chat_id,
        target_session_id,
        target["title"],
        message.get("message_id"),
        request_context=request_context,
    )
    return True


def _session_page(db, token, callback, answer_callback, chat_id, message, session_id, request_context, value):
    answer_callback(token, str(callback.get("id", "")), "Page")
    send_session_menu(
        token,
        chat_id,
        list_sessions(db, chat_id),
        session_id,
        message.get("message_id"),
        int(value.split(":", 1)[1]),
        request_context=request_context,
    )
    return True


def _session_delete(db, token, callback, answer_callback, chat_id, message, session_id, request_context):
    answer_callback(token, str(callback.get("id", "")), "Delete session")
    send_session_delete_menu(
        token,
        chat_id,
        list_sessions(db, chat_id),
        session_id,
        message.get("message_id"),
        request_context=request_context,
    )
    return True


def _session_back(db, token, callback, answer_callback, chat_id, message, session_id, request_context):
    answer_callback(token, str(callback.get("id", "")), "Back")
    send_session_menu(
        token,
        chat_id,
        list_sessions(db, chat_id),
        session_id,
        message.get("message_id"),
        request_context=request_context,
    )
    return True


def _session_cancel(db, token, callback, answer_callback, chat_id, message):
    answer_callback(token, str(callback.get("id", "")), "Cancelled")
    set_meta(db, f"character_session_input:{chat_id}", "")
    close_panel_message(db, token, chat_id, {"message": message})
    return True


def _session_new(db, token, callback, answer_callback, chat_id, message, session, group_service, request_context):
    answer_callback(token, str(callback.get("id", "")), "Enter session name")
    start_session_name_input(
        db,
        token,
        chat_id,
        session,
        message=message,
        group_service=group_service,
        app_settings=request_context.app_settings,
        request_context=request_context,
    )
    return True


def _session_select(db, token, callback, answer_callback, chat_id, operation_id, request_context, value):
    available = {item["session_id"] for item in list_sessions(db, chat_id)}
    if value in available:
        set_meta(db, f"active_session:{chat_id}", value)
        answer_callback(token, str(callback.get("id", "")), "Session selected")
        pending_character = pending_character_for_session(db, chat_id, app_settings=request_context.app_settings)
        if pending_character:
            target_title = next(
                (item["title"] for item in list_sessions(db, chat_id) if item["session_id"] == value), value
            )
            update_session(
                db,
                chat_id,
                value,
                operation_id=operation_id,
                operation_kind="character_select",
                character_file=Path(pending_character["character_file"]).name,
            )
            set_meta(db, f"character_session_input:{chat_id}", "")
        remove_inline_keyboard(db, token, callback)
        if pending_character:
            character_label = pending_character.get("character_name") or Path(pending_character["character_file"]).stem
            send_text(
                token,
                chat_id,
                (
                    "Character selected for session '"
                    f"""{target_title}"""
                    "': "
                    f"{character_label}"
                ),
            )
        else:
            send_text(token, chat_id, f"Session selected: {value}")
    else:
        answer_callback(token, str(callback.get("id", "")), "Session not found")
    return True


def _handle_session(
    db,
    token,
    callback,
    answer_callback,
    data,
    chat_id,
    message,
    session,
    session_id,
    operation_id,
    group_service,
    request_context,
):
    value = data.split(":", 1)[1]
    exact = {
        "delete": lambda: _session_delete(
            db, token, callback, answer_callback, chat_id, message, session_id, request_context
        ),
        "back": lambda: _session_back(
            db, token, callback, answer_callback, chat_id, message, session_id, request_context
        ),
        "cancel": lambda: _session_cancel(db, token, callback, answer_callback, chat_id, message),
        "new": lambda: _session_new(
            db, token, callback, answer_callback, chat_id, message, session, group_service, request_context
        ),
    }
    prefixes = (
        (
            "page:",
            lambda: _session_page(
                db, token, callback, answer_callback, chat_id, message, session_id, request_context, value
            ),
        ),
    )
    handler = exact.get(value)
    if handler is None:
        handler = next((call for prefix, call in prefixes if value.startswith(prefix)), None)
    return (
        handler()
        if handler is not None
        else _session_select(db, token, callback, answer_callback, chat_id, operation_id, request_context, value)
    )


def _bind_routes(
    db,
    token,
    callback,
    answer_callback,
    data,
    chat_id,
    message,
    session,
    session_id,
    operation_id,
    *,
    group_service: GroupService,
    memory_service,
    request_context,
):
    return (
        {
            "session:protected": lambda: _handle_protected(
                db, token, callback, answer_callback, chat_id, message, session_id, request_context
            ),
        },
        (
            (
                "sessiondeleteconfirm:",
                lambda: _handle_delete_confirm(
                    db,
                    token,
                    callback,
                    answer_callback,
                    data,
                    chat_id,
                    session_id,
                    operation_id,
                    memory_service,
                    request_context,
                ),
            ),
            (
                "sessiondelete:",
                lambda: _handle_delete(
                    db, token, callback, answer_callback, data, chat_id, message, session_id, request_context
                ),
            ),
            (
                "session:",
                lambda: _handle_session(
                    db,
                    token,
                    callback,
                    answer_callback,
                    data,
                    chat_id,
                    message,
                    session,
                    session_id,
                    operation_id,
                    group_service,
                    request_context,
                ),
            ),
        ),
    )


def handle_session_callback(
    db,
    token,
    callback,
    answer_callback,
    data,
    chat_id,
    message,
    session,
    session_id,
    operation_id,
    *,
    group_service: GroupService,
    memory_service,
    request_context,
):
    """Dispatch one scoped action, retaining entity selection and deletion safeguards."""
    exact, prefixes = _bind_routes(
        db,
        token,
        callback,
        answer_callback,
        data,
        chat_id,
        message,
        session,
        session_id,
        operation_id,
        group_service=group_service,
        memory_service=memory_service,
        request_context=request_context,
    )
    handler = exact.get(data)
    if handler is None:
        handler = next((call for prefix, call in prefixes if data.startswith(prefix)), None)
    return handler() if handler is not None else False
