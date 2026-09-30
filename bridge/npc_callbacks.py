"""Telegram callback handling for the NPC Bank."""

from __future__ import annotations

from bridge.callbacks import close_panel_message
from bridge.card_content import card_fields_from_file
from bridge.npc_extraction import refresh_npc_state_now
from bridge.npc_panels import (
    send_npc_detail,
    send_npc_history,
    send_npc_menu,
    send_npc_undo_confirm,
)
from bridge.npc_service import NpcService
from bridge.provider_port import ProviderPort


def _parse_npc_id(value: str) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def handle_npc_callback(
    db,
    token: str,
    callback: dict,
    answer_callback,
    data: str,
    chat_id: str,
    message: dict,
    session: dict[str, str],
    fields: dict[str, str] | None = None,
    *,
    npc_service: NpcService,
    provider_port: ProviderPort,
    request_context,
) -> bool:
    if not data.startswith("npc:"):
        return False
    message_id = message.get("message_id")
    resolved_fields = fields or card_fields_from_file(
        session["character_file"],
        app_settings=request_context.app_settings,
    )
    parts = data.split(":")
    action = parts[1] if len(parts) > 1 else ""

    if action == "menu":
        answer_callback(token, str(callback.get("id", "")), "NPC Bank")
        send_npc_menu(
            token,
            chat_id,
            db,
            session,
            message_id,
            npc_service=npc_service,
            request_context=request_context,
        )
        return True
    if action == "page" and len(parts) == 3:
        try:
            page = int(parts[2])
        except ValueError:
            page = 0
        answer_callback(token, str(callback.get("id", "")), "Page")
        send_npc_menu(
            token,
            chat_id,
            db,
            session,
            message_id,
            page,
            npc_service=npc_service,
            request_context=request_context,
        )
        return True
    if action == "refresh":
        updates = refresh_npc_state_now(
            db,
            chat_id,
            session,
            resolved_fields,
            provider_port=provider_port,
            app_settings=request_context.app_settings,
        )
        answer_callback(
            token,
            str(callback.get("id", "")),
            f"NPC Bank refreshed: {updates} updates",
        )
        send_npc_menu(
            token,
            chat_id,
            db,
            session,
            message_id,
            npc_service=npc_service,
            request_context=request_context,
        )
        return True
    if action == "view" and len(parts) == 3:
        npc_id = _parse_npc_id(parts[2])
        if npc_id is None:
            answer_callback(token, str(callback.get("id", "")), "NPC not found")
            return True
        answer_callback(token, str(callback.get("id", "")), "NPC")
        send_npc_detail(
            token,
            chat_id,
            db,
            session,
            resolved_fields,
            npc_id,
            message_id,
            npc_service=npc_service,
            request_context=request_context,
        )
        return True
    if action == "history" and len(parts) == 3:
        npc_id = _parse_npc_id(parts[2])
        if npc_id is None:
            answer_callback(token, str(callback.get("id", "")), "NPC not found")
            return True
        answer_callback(token, str(callback.get("id", "")), "History")
        send_npc_history(
            token,
            chat_id,
            db,
            session,
            resolved_fields,
            npc_id,
            message_id,
            npc_service=npc_service,
            request_context=request_context,
        )
        return True
    if action == "undo" and len(parts) == 5:
        npc_id = _parse_npc_id(parts[2])
        change_id = _parse_npc_id(parts[4])
        if npc_id is None or change_id is None:
            answer_callback(token, str(callback.get("id", "")), "NPC not found")
            return True
        send_npc_undo_confirm(
            token,
            chat_id,
            db,
            session,
            npc_id,
            parts[3],
            change_id,
            message_id,
            npc_service=npc_service,
            request_context=request_context,
        )
        return True
    if action == "undo-confirm" and len(parts) == 5:
        npc_id = _parse_npc_id(parts[2])
        change_id = _parse_npc_id(parts[4])
        if npc_id is None or change_id is None:
            answer_callback(token, str(callback.get("id", "")), "NPC not found")
            return True
        try:
            restored = npc_service.undo_latest_field_change(
                db,
                chat_id,
                session["session_id"],
                npc_id,
                parts[3],
                expected_change_id=change_id,
            )
        except ValueError:
            answer_callback(token, str(callback.get("id", "")), "NPC state changed; refresh history")
            return True
        answer_callback(
            token,
            str(callback.get("id", "")),
            "NPC field restored" if restored else "No NPC change to undo",
        )
        send_npc_detail(
            token,
            chat_id,
            db,
            session,
            resolved_fields,
            npc_id,
            message_id,
            npc_service=npc_service,
            request_context=request_context,
        )
        return True
    if action == "close":
        answer_callback(token, str(callback.get("id", "")), "Closed")
        close_panel_message(db, token, chat_id, callback)
        return True

    answer_callback(token, str(callback.get("id", "")), "NPC action invalid")
    return True
