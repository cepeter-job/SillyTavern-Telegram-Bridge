"""character proposal callbacks owner."""

from __future__ import annotations

import logging
import sqlite3

from bridge.card_content import card_fields_from_file
from bridge.character_optimizer_panels import (
    send_character_optimize_result,
)
from bridge.character_proposals import load_character_proposal
from bridge.character_quality import rank_character
from bridge.native_imports import (
    apply_character_proposal,
)
from bridge.provider_port import ProviderPort
from bridge.telegram import send_panel_request


def _handle_proposal(
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
    """Handle proposal callbacks."""
    try:
        parts = data.split(":")
        if parts[0] == "characteroptimizepreview" and len(parts) == 3:
            pending = load_character_proposal(db, chat_id, parts[1], request_context=request_context)
            if pending.kind != "optimize":
                raise ValueError("invalid optimizer preview")
            send_character_optimize_result(
                token,
                chat_id,
                pending.filename,
                pending.fields,
                pending.nonce,
                message.get("message_id"),
                int(parts[2]),
                request_context=request_context,
            )
            return True
        if parts[0] == "characterupload" and len(parts) == 3:
            action, nonce = parts[1], parts[2]
        elif parts[0] in {"characteroptimizeapply", "characteroptimizecancel"} and len(parts) == 2:
            action = "apply" if parts[0] == "characteroptimizeapply" else "cancel"
            nonce = parts[1]
        else:
            raise ValueError("invalid or expired character confirmation")
        message_text, applied_filename = apply_character_proposal(
            db,
            chat_id,
            nonce,
            action,
            request_context=request_context,
        )
        if applied_filename:
            try:
                rank_character(
                    db,
                    chat_id,
                    session,
                    card_fields_from_file(applied_filename, app_settings=request_context.app_settings),
                    applied_filename,
                    provider_port=provider_port,
                    app_settings=request_context.app_settings,
                )
            except (OSError, ValueError, sqlite3.Error):
                logging.warning("Character applied; optional ranking was unavailable")
    except (ValueError, OSError) as exc:
        message_text = str(exc) if isinstance(exc, ValueError) else "Character update failed; reopen the preview."
        logging.warning("Character proposal was not applied or could not be completed")
    answer_callback(token, str(callback.get("id", "")), "Character proposal processed")
    send_panel_request(
        token,
        "editMessageText",
        {
            "chat_id": chat_id,
            "message_id": message.get("message_id"),
            "text": message_text,
            "reply_markup": {
                "inline_keyboard": [
                    [
                        {"text": "Character menu", "callback_data": "character:menu"},
                        {"text": "Close", "callback_data": "character:cancel"},
                    ]
                ]
            },
        },
        request_context=request_context,
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
        {},
        (
            (
                "characterupload:",
                lambda: _handle_proposal(
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
                "characteroptimizeapply:",
                lambda: _handle_proposal(
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
                "characteroptimizecancel:",
                lambda: _handle_proposal(
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
                "characteroptimizepreview:",
                lambda: _handle_proposal(
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
        ),
    )


def handle_character_proposal_callback(
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
