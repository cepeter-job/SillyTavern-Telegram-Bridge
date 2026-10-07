"""Session-scoped natural-action controls; reading a mode never creates a session."""

import sqlite3

from bridge.meta_repository import load_meta_value, store_meta_value
from bridge.sqlite_store import write_transaction

DEFAULT_MODE = "auto"
MODES = {"auto", "director", "manual"}


def action_mode(db: sqlite3.Connection, chat_id: str, session_id: str) -> str:
    value = load_meta_value(db, f"action_check_mode:{chat_id}:{session_id}", DEFAULT_MODE)
    return value if value in MODES else DEFAULT_MODE


def set_action_mode(db: sqlite3.Connection, chat_id: str, session_id: str, mode: str) -> str:
    if mode not in MODES:
        raise ValueError("Use /check mode auto, /check mode director, or /check mode manual.")
    with write_transaction(db):
        if (
            db.execute("SELECT 1 FROM sessions WHERE chat_id=? AND session_id=?", (chat_id, session_id)).fetchone()
            is None
        ):
            raise ValueError("Choose a story before changing check mode.")
        store_meta_value(db, f"action_check_mode:{chat_id}:{session_id}", mode)
    return mode


def action_mode_status(db: sqlite3.Connection, chat_id: str, session_id: str) -> str:
    mode = action_mode(db, chat_id, session_id)
    description = {
        "auto": "Utility judges meaningful uncertainty before new actions; the bridge rolls once.",
        "director": "Director judges meaningful uncertainty before new actions; the bridge rolls once.",
        "manual": "Only explicit /check commands roll dice.",
    }[mode]
    return (
        f"Action checks: {mode}\n{description}\n"
        "Automatic modes can add one model request before narration. Routine actions need no roll.\n"
        "Use /check mode auto|director|manual. Mode changes affect new actions, not saved results."
    )
