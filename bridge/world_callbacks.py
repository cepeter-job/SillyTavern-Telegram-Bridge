"""Canonical world callbacks owner."""

from __future__ import annotations

import json
import time
from pathlib import Path

from bridge.callback_tokens import dynamic_callback_token, resolve_dynamic_callback_token
from bridge.callbacks import discard_panel_binding, remove_inline_keyboard
from bridge.card_content import active_world_files, encode_world_files, safe_world_path
from bridge.cards import send_panel_message
from bridge.group_panels import send_group_menu
from bridge.group_service import GroupService
from bridge.limits import PENDING_SETTINGS_TTL_SECONDS
from bridge.metadata import set_meta
from bridge.session_core import update_session
from bridge.telegram import send_text
from bridge.world_management import delete_world_info_file
from bridge.world_panels import send_world_menu


def _handle_delete_confirm(db, token, callback, answer_callback, data, chat_id, session, request_context, message_id):
    value = resolve_dynamic_callback_token(data.split(":", 1)[1], "world", chat_id, db=db) or ""
    try:
        delete_world_info_file(db, chat_id, value, app_settings=request_context.app_settings)
    except (ValueError, OSError) as exc:
        answer_callback(token, str(callback.get("id", "")), "Delete refused")
        send_text(token, chat_id, str(exc))
    else:
        answer_callback(token, str(callback.get("id", "")), "World Info deleted")
        send_world_menu(token, chat_id, session["world_file"], message_id, 0, request_context=request_context)
    return True


def _handle_delete(db, token, callback, answer_callback, data, chat_id, request_context, message_id):
    value = resolve_dynamic_callback_token(data.split(":", 1)[1], "world", chat_id, db=db) or ""
    if not safe_world_path(value, app_settings=request_context.app_settings):
        answer_callback(token, str(callback.get("id", "")), "World Info file not found")
        return True
    answer_callback(token, str(callback.get("id", "")), "Confirm deletion")
    send_panel_message(
        token,
        chat_id,
        f"Delete World Info '{Path(value).name}'? This cannot be undone.",
        {
            "inline_keyboard": [
                [
                    {
                        "text": "🗑️ Delete",
                        "callback_data": "worlddeleteconfirm:" + dynamic_callback_token("world", value, chat_id, db=db),
                    },
                    {"text": "Cancel", "callback_data": "world:cancel"},
                ]
            ]
        },
        message_id,
        request_context=request_context,
    )
    return True


def _world_page(token, callback, answer_callback, chat_id, message, session, request_context, value):
    answer_callback(token, str(callback.get("id", "")), "Page")
    send_world_menu(
        token,
        chat_id,
        session["world_file"],
        message.get("message_id"),
        int(value.split(":", 1)[1]),
        request_context=request_context,
    )
    return True


def _world_upload(db, token, callback, answer_callback, chat_id, request_context, message_id):
    pending = {
        "session_id": request_context.session_id,
        "actor_id": request_context.actor_id,
        "expires_at": time.time() + PENDING_SETTINGS_TTL_SECONDS,
    }
    set_meta(db, f"world_upload:{chat_id}", json.dumps(pending))
    answer_callback(token, str(callback.get("id", "")), "Send JSON document")
    discard_panel_binding(db, chat_id, message_id)
    send_text(token, chat_id, "Send the World Info JSON as a Telegram document. Use /cancel to abort.")
    return True


def _world_cancel(db, token, callback, answer_callback, chat_id, setup):
    answer_callback(token, str(callback.get("id", "")), "Cancelled")
    if setup:
        set_meta(db, f"group_setup:{chat_id}", "")
    remove_inline_keyboard(db, token, callback)
    return True


def _world_done(db, token, callback, answer_callback, chat_id, session, group_service, request_context, setup):
    answer_callback(token, str(callback.get("id", "")), "Saved")
    if setup:
        set_meta(db, f"group_setup:{chat_id}", "")
    remove_inline_keyboard(db, token, callback)
    if setup:
        send_group_menu(db, token, chat_id, session, group_service=group_service, request_context=request_context)
    return True


def _world_off(
    db, token, callback, answer_callback, chat_id, message, session, session_id, operation_id, request_context
):
    update_session(db, chat_id, session_id, operation_id=operation_id, operation_kind="world_clear", world_file="")
    session["world_file"] = ""
    answer_callback(token, str(callback.get("id", "")), "All World Info cleared")
    send_world_menu(token, chat_id, "", message.get("message_id"), 0, request_context=request_context)
    return True


def _world_select(
    db, token, callback, answer_callback, chat_id, message, session, session_id, operation_id, request_context, value
):
    if safe_world_path(value, app_settings=request_context.app_settings):
        selected = active_world_files(session["world_file"], app_settings=request_context.app_settings)
        filename = Path(value).name
        if filename in selected:
            selected.remove(filename)
            status = "World Info disabled"
        else:
            selected.append(filename)
            status = "World Info enabled"
        update_session(
            db,
            chat_id,
            session_id,
            operation_id=operation_id,
            operation_kind="world_select",
            world_file=encode_world_files(selected),
        )
        session["world_file"] = encode_world_files(selected)
        answer_callback(token, str(callback.get("id", "")), status)
        send_world_menu(
            token,
            chat_id,
            encode_world_files(selected),
            message.get("message_id"),
            0,
            request_context=request_context,
        )
    else:
        answer_callback(token, str(callback.get("id", "")), "World Info file not found")
    return True


def _handle_world(
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
    message_id,
):
    setup = group_service.setup_state(db, chat_id, session_id)
    value = data.split(":", 1)[1]
    if not value.startswith("page:") and value not in {"cancel", "done", "off", "upload"}:
        value = resolve_dynamic_callback_token(value, "world", chat_id, db=db) or ""
    exact = {
        "upload": lambda: _world_upload(db, token, callback, answer_callback, chat_id, request_context, message_id),
        "cancel": lambda: _world_cancel(db, token, callback, answer_callback, chat_id, setup),
        "done": lambda: _world_done(
            db, token, callback, answer_callback, chat_id, session, group_service, request_context, setup
        ),
        "off": lambda: _world_off(
            db, token, callback, answer_callback, chat_id, message, session, session_id, operation_id, request_context
        ),
    }
    prefixes = (
        (
            "page:",
            lambda: _world_page(token, callback, answer_callback, chat_id, message, session, request_context, value),
        ),
    )
    handler = exact.get(value)
    if handler is None:
        handler = next((call for prefix, call in prefixes if value.startswith(prefix)), None)
    return (
        handler()
        if handler is not None
        else _world_select(
            db,
            token,
            callback,
            answer_callback,
            chat_id,
            message,
            session,
            session_id,
            operation_id,
            request_context,
            value,
        )
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
    request_context,
):
    message_id = message.get("message_id")
    return {}, (
        (
            "worlddeleteconfirm:",
            lambda: _handle_delete_confirm(
                db, token, callback, answer_callback, data, chat_id, session, request_context, message_id
            ),
        ),
        (
            "worlddelete:",
            lambda: _handle_delete(db, token, callback, answer_callback, data, chat_id, request_context, message_id),
        ),
        (
            "world:",
            lambda: _handle_world(
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
                message_id,
            ),
        ),
    )


def handle_world_callback(
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
    request_context,
):
    """Route world actions without mixing upload, deletion and selection ownership."""
    _exact, prefixes = _bind_routes(
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
        request_context=request_context,
    )
    handler = next((call for prefix, call in prefixes if data.startswith(prefix)), None)
    return handler() if handler is not None else False
