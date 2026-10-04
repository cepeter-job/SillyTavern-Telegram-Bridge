"""Actor/session-bound Narrative Style controls; no provider or story generation."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable

from bridge.callback_tokens import resolve_dynamic_callback_token
from bridge.callbacks import close_panel_message
from bridge.narrative_panels import send_narrative_menu
from bridge.narrative_settings import apply_narrative_preference
from bridge.request_types import RequestContext
from bridge.telegram import send_text


def handle_narrative_callback(
    db: sqlite3.Connection,
    token: str,
    callback: dict,
    answer_callback: Callable,
    data: str,
    chat_id: str,
    message: dict,
    session: dict,
    session_id: str,
    operation_id: int | None,
    *,
    request_context: RequestContext,
) -> bool:
    if not data.startswith("narrative:"):
        return False
    try:
        raw = resolve_dynamic_callback_token(data.split(":", 1)[1], "narrative", chat_id, db=db)
        command = json.loads(raw or "{}")
        if (
            not isinstance(command, dict)
            or not request_context.actor_id
            or command.get("actor_id") != request_context.actor_id
            or command.get("session_id") != session_id
            or request_context.session_id != session_id
        ):
            raise ValueError("This Narrative Style panel expired or belongs to another user. Reopen /narrative.")
        action = command.get("action")
        if action == "close":
            close_panel_message(db, token, chat_id, {"message": message})
        else:
            view, field = "presets", ""
            if action == "view":
                view, field = str(command.get("value", "")), str(command.get("field", ""))
            else:
                revision = command.get("settings_revision")
                if type(revision) is not int:
                    raise ValueError("This Narrative Style panel expired. Reopen /narrative.")
                apply_narrative_preference(
                    db,
                    chat_id,
                    session_id,
                    actor_id=request_context.actor_id,
                    expected_revision=revision,
                    action=str(action),
                    value=str(command.get("value", "")),
                    field=str(command.get("field", "")),
                )
                if action == "default":
                    send_text(
                        token,
                        chat_id,
                        "Saved as your default for future character setup. Existing stories are unchanged.",
                    )
                elif action == "set":
                    view = "advanced"
            send_narrative_menu(
                token,
                chat_id,
                session,
                message.get("message_id"),
                request_context=request_context,
                view=view,
                field=field,
            )
        answer_callback(token, str(callback.get("id") or ""), "Narrative Style updated")
    except (ValueError, TypeError, KeyError) as exc:
        answer_callback(token, str(callback.get("id") or ""), "Narrative Style unavailable")
        send_text(
            token,
            chat_id,
            str(exc)
            if isinstance(exc, ValueError)
            else "This Narrative Style panel expired. Reopen /narrative to make a change.",
        )
    return True
