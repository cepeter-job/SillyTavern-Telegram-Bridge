"""Session and actor panel ownership with explicit binding lifetimes."""

from __future__ import annotations

import sqlite3
import time

from bridge.metadata import get_meta, set_meta
from bridge.panel_repository import (
    delete_panel_binding,
    load_panel_binding,
    load_panel_binding_revision,
    store_panel_binding,
)
from bridge.sqlite_store import write_transaction


def bind_panel_session(
    db: sqlite3.Connection,
    chat_id: str,
    message_id: int | str,
    session_id: str,
    owner_user_id: str = "",
) -> None:
    with write_transaction(db):
        store_panel_binding(
            db, str(chat_id), str(message_id), str(session_id), str(owner_user_id or ""), time.time() + 900
        )


def panel_session_for_message(db: sqlite3.Connection, chat_id: str, message_id: int | str) -> str | None:
    row = load_panel_binding(db, str(chat_id), str(message_id), time.time())
    return row[0] if row else None


def panel_owner_for_message(db: sqlite3.Connection, chat_id: str, message_id: int | str) -> str:
    row = load_panel_binding(db, str(chat_id), str(message_id), time.time())
    return row[1] if row else ""


def panel_binding_revision(db: sqlite3.Connection, chat_id: str, message_id: int | str) -> float | None:
    return load_panel_binding_revision(db, str(chat_id), str(message_id), time.time())


def _management_panel_key(chat_id: str, owner_user_id: str) -> str:
    return f"management_panel:{chat_id}:{owner_user_id}"


def active_management_panel(db: sqlite3.Connection, chat_id: str, owner_user_id: str) -> int | None:
    if not owner_user_id:
        return None
    try:
        raw = get_meta(db, _management_panel_key(chat_id, owner_user_id), "")
    except sqlite3.OperationalError:
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def bind_management_panel(db: sqlite3.Connection, chat_id: str, owner_user_id: str, message_id: int | str) -> None:
    if not owner_user_id:
        return
    try:
        set_meta(db, _management_panel_key(chat_id, owner_user_id), str(int(message_id)))
    except sqlite3.OperationalError:
        return


def clear_management_panel(
    db: sqlite3.Connection,
    chat_id: str,
    owner_user_id: str,
    message_id: int | str | None = None,
) -> None:
    if not owner_user_id:
        return
    key = _management_panel_key(chat_id, owner_user_id)
    if message_id is not None and get_meta(db, key, "") != str(message_id):
        return
    try:
        set_meta(db, key, "")
    except sqlite3.OperationalError:
        return


def discard_panel_session(db: sqlite3.Connection, chat_id: str, message_id: int | str) -> None:
    row = load_panel_binding(db, str(chat_id), str(message_id), time.time())
    owner = row[1] if row else ""
    with write_transaction(db):
        delete_panel_binding(db, str(chat_id), str(message_id))
    if owner:
        clear_management_panel(db, chat_id, owner, message_id)
