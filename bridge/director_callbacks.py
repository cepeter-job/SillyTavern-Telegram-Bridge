"""Actor-bound Telegram adapters for revision-checked Director Room actions."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable

from bridge.callback_tokens import resolve_dynamic_callback_token
from bridge.callbacks import close_panel_message
from bridge.director_input import begin_director_input
from bridge.director_panels import send_director_menu
from bridge.director_room import apply_controls, apply_direction, require_room_revision, steer_thread
from bridge.director_service import DirectorService
from bridge.model_selection import director_reasoning_for_session
from bridge.narrative_panels import send_narrative_menu
from bridge.persona_service import PersonaService
from bridge.provider_port import ProviderPort
from bridge.request_types import RequestContext
from bridge.telegram import send_text


def handle_director_callback(
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
    provider_port: ProviderPort,
    persona_service: PersonaService,
    request_context: RequestContext,
) -> bool:
    if not data.startswith("director:"):
        return False
    try:
        raw = resolve_dynamic_callback_token(data.split(":", 1)[1], "director", chat_id, db=db)
        values = json.loads(raw or "{}")
        if not isinstance(values, dict) or not values:
            raise ValueError("This Director panel expired. Reopen /director.")
        if not request_context.actor_id or values.get("actor_id") != request_context.actor_id:
            raise ValueError("This Director panel belongs to another user.")
        if values.get("session_id") != session_id or request_context.session_id != session_id:
            raise ValueError("This Director panel belongs to another story.")
        action = str(values.get("action", ""))
        page = "main"
        if action == "close":
            close_panel_message(db, token, chat_id, {"message": message})
        elif action == "narrative":
            send_narrative_menu(token, chat_id, session, message.get("message_id"), request_context=request_context)
        else:
            if action not in {"open", "history", "arcs", "ending"}:
                revision = str(values.get("revision", ""))
                require_room_revision(db, chat_id, session_id, revision)
                if action == "edit":
                    scope = str(values.get("scope", ""))
                    begin_director_input(
                        db,
                        chat_id,
                        session_id,
                        request_context.actor_id,
                        revision,
                        scope,
                        arc_id=str(values.get("arc_id", "")),
                    )
                    prompt = (
                        "Send the direction for the next scene."
                        if scope == "next_scene"
                        else ("Send a persistent Director objective. It stays active until you change or clear it.")
                    )
                    if scope == "cadence":
                        prompt = "Send a whole-number Director interval from 1 to 100 completed Story turns."
                    if scope == "ending_goal":
                        prompt = (
                            "Send the optional ending goal (up to 4,000 characters), or /clear for an emergent ending."
                        )
                    elif scope == "arc_note":
                        prompt = (
                            "Send future guidance for this arc (up to 1,000 characters), or /clear to remove it. "
                            "This does not change its established outcome."
                        )
                    send_text(token, chat_id, prompt + " Use /cancel to keep the current plan.")
                elif action == "clear_objective":
                    apply_direction(db, chat_id, session_id, revision, "", "persistent")
                elif action == "cadence":
                    page = "cadence"
                elif action == "set_cadence":
                    apply_controls(
                        db,
                        chat_id,
                        session_id,
                        revision,
                        cadence=values.get("cadence"),
                        interval=values.get("interval"),
                        reasoning=director_reasoning_for_session(db, chat_id, session_id),
                    )
                    page = "cadence"
                elif action == "threads":
                    page = "threads"
                elif action == "thread":
                    steer_thread(
                        db,
                        chat_id,
                        session,
                        revision,
                        str(values.get("thread_id", "")),
                        persona_service=persona_service,
                        app_settings=request_context.app_settings,
                    )
                elif action == "reassess":
                    result = DirectorService().reassess(
                        db,
                        request_context.app_settings.api_key,
                        chat_id,
                        session,
                        reason="manual",
                        provider_port=provider_port,
                        persona_service=persona_service,
                        app_settings=request_context.app_settings,
                    )
                    if result.result != "accepted":
                        send_text(token, chat_id, result.reason)
                else:
                    raise ValueError("This Director action expired. Reopen /director.")
            if action in {"history", "arcs", "ending"}:
                page = action
            send_director_menu(
                token, chat_id, session, message.get("message_id"), request_context=request_context, page=page
            )
        answer_callback(token, str(callback.get("id") or ""), "Director Room")
    except (ValueError, TypeError, KeyError) as exc:
        answer_callback(token, str(callback.get("id") or ""), "Director action unavailable")
        send_text(
            token,
            chat_id,
            str(exc) if isinstance(exc, ValueError) else "This Director panel expired. Reopen /director.",
        )
    return True
