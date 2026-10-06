"""character callbacks owner."""

from __future__ import annotations

import json
import logging
import time

from bridge.callback_tokens import resolve_dynamic_callback_token
from bridge.callbacks import close_panel_message, discard_panel_binding
from bridge.card_content import card_fields_from_file, safe_character_path
from bridge.cards import (
    character_rank_label,
    send_character_delete_confirm,
    send_character_delete_menu,
    send_character_info_menu,
    send_character_menu,
    send_character_restore_confirm,
    send_character_restore_menu,
)
from bridge.character_backups import character_restore_targets
from bridge.character_quality import character_rank
from bridge.conversation_setup import begin_setup
from bridge.conversation_setup_panels import send_setup_panel
from bridge.group_service import GroupService
from bridge.group_setup import apply_group_setup_character
from bridge.limits import PENDING_SETTINGS_TTL_SECONDS
from bridge.metadata import get_meta, set_meta
from bridge.native_imports import (
    character_card_digest,
    character_delete_references,
    restore_character_card_backup,
    verify_character_card_backup,
)
from bridge.operations import begin_operation, record_operation
from bridge.provider_port import ProviderPort
from bridge.telegram import send_panel_photo, send_panel_request


def _character_info_text(info: dict, filename: str, rank: str | None) -> str:
    return (
        "Character: "
        f"{info['name']}"
        "\nRank: "
        f"{character_rank_label(rank)}"
        "\nFile: "
        f"{filename}"
        "\nDescription: "
        f"{len(info['description'])}"
        " chars\nPersonality: "
        f"{len(info['personality'])}"
        " chars\nScenario: "
        f"{len(info['scenario'])}"
        " chars\nFirst message: "
        f"{len(info['first_mes'])}"
        " chars"
    )


def _handle_protected(token, callback, answer_callback, chat_id, message, session, *, request_context) -> bool:
    """Handle protected callbacks."""
    message_id = message.get("message_id")
    answer_callback(token, str(callback.get("id", "")), "Active/default character is protected")
    send_character_menu(token, chat_id, session["character_file"], message_id, request_context=request_context)
    return True


def _handle_menu(db, token, callback, answer_callback, chat_id, message, session, *, request_context) -> bool:
    set_meta(db, f"character_upload:{chat_id}:{request_context.actor_id}", "")
    answer_callback(token, str(callback.get("id", "")), "Refreshed")
    send_character_menu(
        token, chat_id, session["character_file"], message.get("message_id"), request_context=request_context
    )
    return True


def _handle_info_menu(token, callback, answer_callback, chat_id, message, session, *, request_context) -> bool:
    """Handle info menu callbacks."""
    answer_callback(token, str(callback.get("id", "")), "Info")
    send_character_info_menu(
        token,
        chat_id,
        message.get("message_id"),
        current_character=session["character_file"],
        request_context=request_context,
    )
    return True


def _handle_delete_menu(token, callback, answer_callback, chat_id, message, session, *, request_context) -> bool:
    """Handle delete menu callbacks."""
    answer_callback(token, str(callback.get("id", "")), "Delete")
    send_character_delete_menu(
        token, chat_id, session["character_file"], message.get("message_id"), request_context=request_context
    )
    return True


def _handle_upload_menu(db, token, callback, answer_callback, chat_id, message, *, request_context) -> bool:
    """Handle upload menu callbacks."""
    set_meta(
        db,
        f"character_upload:{chat_id}:{request_context.actor_id}",
        json.dumps(
            {
                "session_id": request_context.session_id,
                "actor_id": request_context.actor_id,
                "expires_at": time.time() + PENDING_SETTINGS_TTL_SECONDS,
            }
        ),
    )
    answer_callback(token, str(callback.get("id", "")), "Upload")
    send_panel_request(
        token,
        "editMessageText",
        {
            "chat_id": chat_id,
            "message_id": message.get("message_id"),
            "text": (
                "Send the character card as a Telegram Document (PNG with SillyTavern "
                "chara metadata). The upload will be validated and queued safely."
            ),
            "reply_markup": {
                "inline_keyboard": [
                    [
                        {"text": "⬅️ Back", "callback_data": "character:menu"},
                        {"text": "❌ Close", "callback_data": "character:cancel"},
                    ]
                ]
            },
        },
        request_context=request_context,
    )
    return True


def _handle_restore_menu(token, callback, answer_callback, chat_id, message, *, request_context) -> bool:
    """Handle restore menu callbacks."""
    answer_callback(token, str(callback.get("id", "")), "Restore backup")
    send_character_restore_menu(
        token,
        chat_id,
        message.get("message_id"),
        request_context=request_context,
    )
    return True


def _handle_info(db, token, callback, answer_callback, data, chat_id, message, session, *, request_context) -> bool:
    """Handle info callbacks."""
    value = data.split(":", 1)[1]
    if value == "back":
        answer_callback(token, str(callback.get("id", "")), "Back")
        close_panel_message(db, token, chat_id, callback)
        send_character_info_menu(
            token,
            chat_id,
            current_character=session["character_file"],
            request_context=request_context,
        )
        return True
    if value.startswith("page:"):
        send_character_info_menu(
            token,
            chat_id,
            message.get("message_id"),
            int(value.split(":", 1)[1]),
            current_character=session["character_file"],
            request_context=request_context,
        )
        return True
    filename = resolve_dynamic_callback_token(value, "character", chat_id, db=db) or ""
    path = safe_character_path(filename, app_settings=request_context.app_settings)
    if not path:
        answer_callback(token, str(callback.get("id", "")), "Character choice expired")
        return True
    info = card_fields_from_file(filename, app_settings=request_context.app_settings)
    rank = character_rank(db, filename, app_settings=request_context.app_settings)
    answer_callback(token, str(callback.get("id", "")), "Info")
    text = _character_info_text(info, filename, rank)
    reply_markup = {
        "inline_keyboard": [
            [
                {"text": "⬅️ Back", "callback_data": "characterinfo:back"},
                {"text": "❌ Close", "callback_data": "character:cancel"},
            ]
        ]
    }
    try:
        send_panel_photo(
            token,
            chat_id,
            path,
            text,
            reply_markup,
            request_context=request_context,
        )
    except (OSError, RuntimeError, ValueError):
        logging.info("Character photo preview unavailable; using text-only info panel", exc_info=True)
        send_panel_request(
            token,
            "editMessageText",
            {
                "chat_id": chat_id,
                "message_id": message.get("message_id"),
                "text": text,
                "reply_markup": {
                    "inline_keyboard": [
                        [
                            {"text": "⬅️ Back", "callback_data": "character:info"},
                            {"text": "❌ Close", "callback_data": "character:cancel"},
                        ]
                    ]
                },
            },
            request_context=request_context,
        )
        return True
    close_panel_message(db, token, chat_id, callback)
    return True


def _handle_restore_selection(db, token, callback, answer_callback, data, chat_id, message, *, request_context) -> bool:
    """Handle restore selection callbacks."""
    value = data.split(":", 1)[1]
    if value.startswith("page:"):
        send_character_restore_menu(
            token,
            chat_id,
            message.get("message_id"),
            int(value.split(":", 1)[1]),
            request_context=request_context,
        )
        return True
    filename = resolve_dynamic_callback_token(value, "character_restore", chat_id, db=db) or ""
    if filename not in character_restore_targets(app_settings=request_context.app_settings):
        answer_callback(token, str(callback.get("id", "")), "Backup choice invalid")
        return True
    try:
        expected_digest = character_card_digest(filename, app_settings=request_context.app_settings)
    except (OSError, ValueError):
        answer_callback(token, str(callback.get("id", "")), "Character state is unavailable")
        return True
    answer_callback(token, str(callback.get("id", "")), "Confirm restore")
    send_character_restore_confirm(
        token,
        chat_id,
        filename,
        expected_digest,
        message.get("message_id"),
        request_context=request_context,
    )
    return True


def _handle_restore_confirm(
    db, token, callback, answer_callback, data, chat_id, message, session, operation_id, *, request_context
) -> bool:
    """Handle restore confirm callbacks."""
    raw_state = resolve_dynamic_callback_token(
        data.split(":", 1)[1],
        "character_restore_confirm",
        chat_id,
        db=db,
    )
    try:
        state = json.loads(raw_state or "")
        filename = str(state["filename"])
        expected_digest = str(state["expected_digest"])
    except (TypeError, ValueError, KeyError, json.JSONDecodeError):
        answer_callback(token, str(callback.get("id", "")), "Restore confirmation expired")
        return True
    if filename not in character_restore_targets(app_settings=request_context.app_settings):
        answer_callback(token, str(callback.get("id", "")), "Restore backup is unavailable")
        return True
    if operation_id is not None and not begin_operation(db, operation_id, "character_restore"):
        answer_callback(token, str(callback.get("id", "")), "Already processed")
        return True
    try:
        restored = restore_character_card_backup(
            filename,
            expected_digest=expected_digest,
            app_settings=request_context.app_settings,
        )
    except (OSError, ValueError) as exc:
        answer_callback(token, str(callback.get("id", "")), f"Restore refused: {exc}")
        return True
    record_operation(db, operation_id, "character_restore")
    db.commit()
    answer_callback(token, str(callback.get("id", "")), "Restored")
    send_character_menu(
        token,
        chat_id,
        session["character_file"],
        message.get("message_id"),
        request_context=request_context,
    )
    logging.info("Restored character backup %s", restored.name)
    return True


def _handle_delete_selection(
    db, token, callback, answer_callback, data, chat_id, message, session, *, request_context
) -> bool:
    """Handle delete selection callbacks."""
    value = data.split(":", 1)[1]
    if value.startswith("page:"):
        send_character_delete_menu(
            token,
            chat_id,
            session["character_file"],
            message.get("message_id"),
            int(value.split(":", 1)[1]),
            request_context=request_context,
        )
        return True
    filename = resolve_dynamic_callback_token(value, "character", chat_id, db=db) or ""
    if (
        not safe_character_path(filename, app_settings=request_context.app_settings)
        or filename == session["character_file"]
    ):
        answer_callback(token, str(callback.get("id", "")), "Character choice invalid")
        return True
    answer_callback(token, str(callback.get("id", "")), "Confirm deletion")
    send_character_delete_confirm(token, chat_id, filename, message.get("message_id"), request_context=request_context)
    return True


def _handle_delete_confirm(
    db, token, callback, answer_callback, data, chat_id, message, session, operation_id, *, request_context
) -> bool:
    """Handle delete confirm callbacks."""
    message_id = message.get("message_id")
    filename = resolve_dynamic_callback_token(data.split(":", 1)[1], "character", chat_id, db=db) or ""
    path = safe_character_path(filename, app_settings=request_context.app_settings)
    references = character_delete_references(db, filename) if path else []
    is_default = filename == request_context.app_settings.default_character_file or (
        path and path.resolve() == request_context.app_settings.card_file.resolve()
    )
    if not path or filename == session["character_file"] or is_default or references:
        reason = "active/default/referenced character" if path else "character not found"
        answer_callback(token, str(callback.get("id", "")), f"Deletion refused: {reason}")
        send_character_menu(token, chat_id, session["character_file"], message_id, request_context=request_context)
        return True
    try:
        verify_character_card_backup(path, path.read_bytes(), app_settings=request_context.app_settings)
    except OSError:
        answer_callback(token, str(callback.get("id", "")), "Deletion refused: backup verification failed")
        return True
    if operation_id is not None and not begin_operation(db, operation_id, "character_delete"):
        answer_callback(token, str(callback.get("id", "")), "Already processed")
        return True
    path.unlink(missing_ok=True)
    record_operation(db, operation_id, "character_delete")
    db.commit()
    answer_callback(token, str(callback.get("id", "")), "Deleted")
    send_character_menu(
        token, chat_id, session["character_file"], message.get("message_id"), request_context=request_context
    )
    return True


def _handle_selection(
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
) -> bool:
    """Handle selection callbacks."""
    value = data.split(":", 1)[1]
    if not value.startswith("page:") and value != "cancel":
        value = resolve_dynamic_callback_token(value, "character", chat_id, db=db) or ""
    if value.startswith("page:"):
        answer_callback(token, str(callback.get("id", "")), "Page")
        send_character_menu(
            token,
            chat_id,
            session["character_file"],
            message.get("message_id"),
            int(value.split(":", 1)[1]),
            request_context=request_context,
        )
        return True
    if value == "cancel":
        answer_callback(token, str(callback.get("id", "")), "Cancelled")
        set_meta(db, f"character_upload:{chat_id}:{request_context.actor_id}", "")
        if group_service.setup_state(db, chat_id, session_id):
            set_meta(db, f"group_setup:{chat_id}", "")
        if get_meta(db, f"character_session_input:{chat_id}", ""):
            set_meta(db, f"character_session_input:{chat_id}", "")
        discard_panel_binding(db, chat_id, message.get("message_id"))
        close_panel_message(db, token, chat_id, callback)
    elif safe_character_path(value, app_settings=request_context.app_settings):
        character_name = card_fields_from_file(value, app_settings=request_context.app_settings)["name"]
        setup = group_service.setup_state(db, chat_id, session_id)
        if setup and setup.get("stage") == "character":
            return apply_group_setup_character(
                db,
                token,
                callback,
                answer_callback,
                chat_id,
                message,
                session_id,
                operation_id,
                value,
                character_name,
                group_service=group_service,
                request_context=request_context,
            )
        state = begin_setup(
            db, chat_id, session, request_context.actor_id, value, app_settings=request_context.app_settings
        )
        answer_callback(token, str(callback.get("id", "")), "Choose conversation mode")
        send_setup_panel(token, chat_id, state, message.get("message_id"), request_context=request_context)

    else:
        answer_callback(token, str(callback.get("id", "")), "Character not found")
    return True


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
    provider_port: ProviderPort,
    request_context,
):
    """Bind only the request values each concrete operation needs."""
    return (
        {
            "character:protected": lambda: _handle_protected(
                token, callback, answer_callback, chat_id, message, session, request_context=request_context
            ),
            "character:menu": lambda: _handle_menu(
                db, token, callback, answer_callback, chat_id, message, session, request_context=request_context
            ),
            "character:info": lambda: _handle_info_menu(
                token, callback, answer_callback, chat_id, message, session, request_context=request_context
            ),
            "character:delete": lambda: _handle_delete_menu(
                token, callback, answer_callback, chat_id, message, session, request_context=request_context
            ),
            "character:upload": lambda: _handle_upload_menu(
                db, token, callback, answer_callback, chat_id, message, request_context=request_context
            ),
            "character:restore": lambda: _handle_restore_menu(
                token, callback, answer_callback, chat_id, message, request_context=request_context
            ),
        },
        (
            (
                "characterinfo:",
                lambda: _handle_info(
                    db,
                    token,
                    callback,
                    answer_callback,
                    data,
                    chat_id,
                    message,
                    session,
                    request_context=request_context,
                ),
            ),
            (
                "characterrestore:",
                lambda: _handle_restore_selection(
                    db, token, callback, answer_callback, data, chat_id, message, request_context=request_context
                ),
            ),
            (
                "characterrestoreconfirm:",
                lambda: _handle_restore_confirm(
                    db,
                    token,
                    callback,
                    answer_callback,
                    data,
                    chat_id,
                    message,
                    session,
                    operation_id,
                    request_context=request_context,
                ),
            ),
            (
                "characterdelete:",
                lambda: _handle_delete_selection(
                    db,
                    token,
                    callback,
                    answer_callback,
                    data,
                    chat_id,
                    message,
                    session,
                    request_context=request_context,
                ),
            ),
            (
                "characterdeleteconfirm:",
                lambda: _handle_delete_confirm(
                    db,
                    token,
                    callback,
                    answer_callback,
                    data,
                    chat_id,
                    message,
                    session,
                    operation_id,
                    request_context=request_context,
                ),
            ),
            (
                "character:",
                lambda: _handle_selection(
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
                ),
            ),
        ),
    )


def handle_character_callback(
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
    provider_port: ProviderPort,
    request_context,
) -> bool:
    """Dispatch exact actions before ordered prefixes, preserving request scope."""
    exact_routes, prefix_routes = _bind_routes(
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
        provider_port=provider_port,
        request_context=request_context,
    )
    handler = exact_routes.get(data)
    if handler is None:
        handler = next((candidate for prefix, candidate in prefix_routes if data.startswith(prefix)), None)
    return handler() if handler is not None else False
