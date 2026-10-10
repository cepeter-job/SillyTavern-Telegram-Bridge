"""Durable Telegram panel busy-state helpers for queued callbacks."""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable

from bridge.conversation_setup_state import setup_panel_markup
from bridge.panel_bindings import panel_binding_revision

BUSY_CALLBACK_DATA = "panelbusy"
BUSY_REPLY_MARKUP = {
    "inline_keyboard": [[{"text": "⏳ Processing…", "callback_data": BUSY_CALLBACK_DATA}]],
}


def _not_modified(exc: BaseException) -> bool:
    return "message is not modified" in str(exc).casefold()


def remember_panel_busy_state(db: sqlite3.Connection, chat_id: str, callback: dict) -> bool:
    message = callback.get("message") or {}
    message_id = message.get("message_id")
    markup = message.get("reply_markup")
    if not message_id or not isinstance(markup, dict):
        return False
    if str(callback.get("data") or "").startswith("setup:"):
        actor_id = str((callback.get("from") or {}).get("id") or "")
        try:
            markup = setup_panel_markup(db, chat_id, actor_id, message_id)
        except sqlite3.Error:
            return False
        if markup is None:
            return False
    revision = panel_binding_revision(db, chat_id, message_id)
    if revision is None:
        return False
    callback["_panel_busy_revision"] = revision
    callback["_panel_original_reply_markup"] = markup
    return True


def mark_panel_busy(request: Callable, token: str, chat_id: str, callback: dict) -> bool:
    message = callback.get("message") or {}
    message_id = message.get("message_id")
    if "_panel_busy_revision" not in callback or not message_id:
        return False
    try:
        request(
            token,
            "editMessageReplyMarkup",
            {"chat_id": chat_id, "message_id": message_id, "reply_markup": BUSY_REPLY_MARKUP},
        )
    except RuntimeError as exc:
        if _not_modified(exc):
            return True
        logging.info("Could not mark panel busy", exc_info=True)
        return False
    except Exception:
        logging.info("Could not mark panel busy", exc_info=True)
        return False
    return True


def restore_busy_panel_if_unchanged(
    request: Callable,
    token: str,
    db: sqlite3.Connection,
    chat_id: str,
    callback: dict,
) -> bool:
    message = callback.get("message") or {}
    message_id = message.get("message_id")
    original_markup = callback.get("_panel_original_reply_markup")
    original_revision = callback.get("_panel_busy_revision")
    if not message_id or not isinstance(original_markup, dict) or not isinstance(original_revision, (int, float)):
        return False
    if panel_binding_revision(db, chat_id, message_id) != float(original_revision):
        return False
    try:
        request(
            token,
            "editMessageReplyMarkup",
            {"chat_id": chat_id, "message_id": message_id, "reply_markup": original_markup},
        )
    except RuntimeError as exc:
        if _not_modified(exc):
            return True
        logging.info("Could not restore busy panel keyboard", exc_info=True)
        return False
    except Exception:
        logging.info("Could not restore busy panel keyboard", exc_info=True)
        return False
    return True
