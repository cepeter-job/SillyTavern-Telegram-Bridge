"""character optimizer callbacks owner."""

from __future__ import annotations

import hashlib
import logging

from bridge.callback_tokens import resolve_dynamic_callback_token
from bridge.card_content import safe_character_path
from bridge.character_optimizer import prepare_character_optimization
from bridge.character_optimizer_input import start_character_optimizer_suggestion_input
from bridge.character_optimizer_panels import (
    send_character_optimize_menu,
    send_character_optimize_options,
    send_character_optimize_result,
)
from bridge.character_proposals import load_character_proposal
from bridge.provider_errors import ProviderRequestError
from bridge.provider_port import ProviderPort
from bridge.telegram import send_panel_request


def _safe_preview_failure_reason(exc: BaseException) -> str:
    text = str(exc).casefold()
    if "invalid json" in text:
        return "invalid optimizer JSON response"
    if "png" in text:
        return "character PNG metadata failed validation"
    if "changed" in text:
        return "character changed during optimization"
    if "size" in text or "limit" in text:
        return "optimized card exceeded a safety limit"
    return "preview validation rejected the generated result"


def _handle_optimizer_menu(token, callback, answer_callback, chat_id, message, session, *, request_context) -> bool:
    """Handle optimizer menu callbacks."""
    answer_callback(token, str(callback.get("id", "")), "Optimizer")
    send_character_optimize_menu(
        token,
        chat_id,
        message.get("message_id"),
        current_character=session["character_file"],
        request_context=request_context,
    )
    return True


def _handle_refine(db, token, callback, answer_callback, data, chat_id, *, request_context) -> bool:
    """Handle refine callbacks."""
    nonce = data.split(":", 1)[1]
    try:
        pending = load_character_proposal(db, chat_id, nonce, request_context=request_context)
        if pending.kind != "optimize":
            raise ValueError("invalid optimizer preview")
        start_character_optimizer_suggestion_input(
            db,
            token,
            chat_id,
            pending.filename,
            pending.expected_digest,
            callback,
            base_fields=pending.fields,
            request_context=request_context,
        )
        answer_callback(token, str(callback.get("id", "")), "Send optimizer suggestion")
    except (OSError, ValueError):
        answer_callback(token, str(callback.get("id", "")), "Optimizer preview expired; reopen it")
    return True


def _handle_prepare(
    db,
    token,
    callback,
    answer_callback,
    data,
    chat_id,
    message,
    session,
    *,
    provider_port: ProviderPort,
    request_context,
) -> bool:
    """Handle prepare callbacks."""
    action, value = data.split(":", 1)
    filename = resolve_dynamic_callback_token(value, "character", chat_id, db=db) or ""
    path = safe_character_path(filename, app_settings=request_context.app_settings)
    if not path or path.is_symlink():
        answer_callback(token, str(callback.get("id", "")), "Character choice expired")
        return True
    if action == "characteroptimizemanual":
        try:
            start_character_optimizer_suggestion_input(
                db,
                token,
                chat_id,
                filename,
                hashlib.sha256(path.read_bytes()).hexdigest(),
                callback,
                request_context=request_context,
            )
            answer_callback(token, str(callback.get("id", "")), "Send optimizer suggestion")
        except (OSError, ValueError):
            answer_callback(token, str(callback.get("id", "")), "Character changed; reopen the optimizer")
        return True
    answer_callback(token, str(callback.get("id", "")), "Optimizing")
    try:
        draft = prepare_character_optimization(
            db,
            chat_id,
            session,
            filename,
            provider_port=provider_port,
            request_context=request_context,
        )
        send_character_optimize_result(
            token,
            chat_id,
            draft.filename,
            draft.fields,
            draft.nonce,
            message.get("message_id"),
            request_context=request_context,
        )
    except ProviderRequestError as exc:
        send_panel_request(
            token,
            "editMessageText",
            {
                "chat_id": chat_id,
                "message_id": message.get("message_id"),
                "text": f"{exc}\n\nThe installed card is unchanged.",
                "reply_markup": {"inline_keyboard": [[{"text": "Back", "callback_data": "character:optimize"}]]},
            },
            request_context=request_context,
        )
    except (ValueError, OSError) as exc:
        logging.warning("Character optimizer preview rejected: %s", exc, exc_info=True)
        reason = _safe_preview_failure_reason(exc)
        send_panel_request(
            token,
            "editMessageText",
            {
                "chat_id": chat_id,
                "message_id": message.get("message_id"),
                "text": (
                    "Optimization response was received, but the preview could not be validated.\n\n"
                    f"Reason: {reason}.\n\n"
                    "The installed card is unchanged."
                ),
                "reply_markup": {"inline_keyboard": [[{"text": "Back", "callback_data": "character:optimize"}]]},
            },
            request_context=request_context,
        )
    return True


def _handle_optimizer_selection(
    db, token, callback, answer_callback, data, chat_id, message, session, *, request_context
) -> bool:
    """Handle optimizer selection callbacks."""
    value = data.split(":", 1)[1]
    if value.startswith("page:"):
        try:
            page = int(value.split(":", 1)[1])
        except ValueError:
            page = 0
        send_character_optimize_menu(
            token,
            chat_id,
            message.get("message_id"),
            page,
            current_character=session["character_file"],
            request_context=request_context,
        )
        return True
    filename = resolve_dynamic_callback_token(value, "character", chat_id, db=db) or ""
    path = safe_character_path(filename, app_settings=request_context.app_settings)
    if not path or path.is_symlink():
        answer_callback(token, str(callback.get("id", "")), "Character choice expired")
        return True
    answer_callback(token, str(callback.get("id", "")), "Optimizer options")
    send_character_optimize_options(
        token, chat_id, filename, message.get("message_id"), request_context=request_context
    )
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
    provider_port: ProviderPort,
    request_context,
):
    """Bind only the request values each concrete operation needs."""
    return (
        {
            "character:optimize": lambda: _handle_optimizer_menu(
                token, callback, answer_callback, chat_id, message, session, request_context=request_context
            ),
        },
        (
            (
                "characteroptimizerefine:",
                lambda: _handle_refine(
                    db, token, callback, answer_callback, data, chat_id, request_context=request_context
                ),
            ),
            (
                "characteroptimizeauto:",
                lambda: _handle_prepare(
                    db,
                    token,
                    callback,
                    answer_callback,
                    data,
                    chat_id,
                    message,
                    session,
                    provider_port=provider_port,
                    request_context=request_context,
                ),
            ),
            (
                "characteroptimizemanual:",
                lambda: _handle_prepare(
                    db,
                    token,
                    callback,
                    answer_callback,
                    data,
                    chat_id,
                    message,
                    session,
                    provider_port=provider_port,
                    request_context=request_context,
                ),
            ),
            (
                "characteroptimize:",
                lambda: _handle_optimizer_selection(
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
        ),
    )


def handle_character_optimizer_callback(
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
        provider_port=provider_port,
        request_context=request_context,
    )
    handler = exact_routes.get(data)
    if handler is None:
        handler = next((candidate for prefix, candidate in prefix_routes if data.startswith(prefix)), None)
    return handler() if handler is not None else False
