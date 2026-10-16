"""Actor-scoped draft keys and the last successfully rendered setup keyboard."""

from __future__ import annotations

import json
import sqlite3

from bridge.metadata import get_meta, set_meta
from bridge.sqlite_store import write_transaction


def setup_key(chat_id: str, actor_id: str) -> str:
    return f"conversation_setup:{chat_id}:{actor_id}"


def setup_panel_markup(db: sqlite3.Connection, chat_id: str, actor_id: str, message_id: int | None) -> dict | None:
    """Return a tracked render only for this exact message, without resolving tokens."""
    try:
        state = json.loads(get_meta(db, setup_key(chat_id, actor_id), "") or "{}")
    except (ValueError, TypeError):
        return None
    if not isinstance(state, dict) or state.get("panel_message_id") != message_id:
        return None
    markup = state.get("panel_reply_markup")
    return markup if isinstance(markup, dict) else None


def remember_setup_render(
    db: sqlite3.Connection, chat_id: str, actor_id: str, nonce: str, message_id: int, markup: dict
) -> None:
    """Bind the successful render without resurrecting or replacing another draft."""
    with write_transaction(db):
        key = setup_key(chat_id, actor_id)
        current = json.loads(get_meta(db, key, "") or "{}")
        if isinstance(current, dict) and current.get("nonce") == nonce:
            current["panel_message_id"] = message_id
            current["panel_reply_markup"] = markup
            set_meta(db, key, json.dumps(current, ensure_ascii=False))
