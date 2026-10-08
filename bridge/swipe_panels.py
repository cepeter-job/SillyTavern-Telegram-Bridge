"""Canonical swipe panels owner."""

from __future__ import annotations

import sqlite3

from bridge.delivery_port import DeliveryPort
from bridge.metadata import set_meta
from bridge.response_variants import last_user_variants, swipe_source_token, swipe_state_key
from bridge.sqlite_store import write_transaction
from bridge.telegram_output import telegram_safe_output


def swipe_markup(source_token: str) -> dict:
    return {
        "inline_keyboard": [
            [
                {"text": "⬅️ Previous", "callback_data": "swipe:prev:" + source_token},
                {"text": "Next ➡️", "callback_data": "swipe:next:" + source_token},
            ],
            [
                {"text": "✅ Keep", "callback_data": "swipe:keep:" + source_token},
                {"text": "❌ Cancel", "callback_data": "swipe:cancel"},
            ],
        ]
    }


def send_swipe_menu(
    token: str, db: sqlite3.Connection, chat_id: str, session_id: str, *, delivery_port: DeliveryPort, request_context
) -> None:
    with write_transaction(db):
        user_row, variants = last_user_variants(db, chat_id, session_id)
        source_token = swipe_source_token(db, chat_id, session_id, int(user_row[0])) if user_row else ""
    if not user_row or not variants:
        delivery_port.send_text(
            token, chat_id, "Belum ada response variant. Kirim pesan lalu gunakan /regen terlebih dahulu."
        )
        return
    selected = next((int(row[0]) for row in variants if row[2]), int(variants[-1][0]))
    set_meta(db, swipe_state_key(chat_id, session_id), str(selected))
    response = telegram_safe_output(next((row[1] for row in variants if int(row[0]) == selected), variants[-1][1]))
    text = f"Variant {selected} of {len(variants)}\n\n{response[:3900]}"
    result = delivery_port.send_panel_request(
        token,
        "sendMessage",
        {"chat_id": chat_id, "text": text, "reply_markup": swipe_markup(source_token)},
        request_context=request_context,
    )
    if result.get("message_id"):
        set_meta(db, f"swipe_message:{chat_id}:{session_id}", str(result["message_id"]))


def edit_swipe_menu(
    token: str,
    db: sqlite3.Connection,
    callback: dict,
    session_id: str,
    index: int,
    variants,
    *,
    source_token: str,
    delivery_port: DeliveryPort,
    request_context,
) -> None:
    message = callback.get("message") or {}
    chat_id = str((message.get("chat") or {}).get("id", ""))
    message_id = message.get("message_id")
    response = telegram_safe_output(next(row[1] for row in variants if int(row[0]) == index))
    delivery_port.send_panel_request(
        token,
        "editMessageText",
        {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": f"Variant {index} of {len(variants)}\n\n{response[:3900]}",
            "reply_markup": swipe_markup(source_token),
        },
        request_context=request_context,
    )
    set_meta(db, swipe_state_key(chat_id, session_id), str(index))
