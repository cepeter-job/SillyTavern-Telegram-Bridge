"""Best-effort target-only memory initialization; never query the source namespace."""

from __future__ import annotations

import sqlite3

from bridge.memory_backend import seed_session_memory_now
from bridge.settings import AppSettings


def seed_alternate_ending_memory(
    db: sqlite3.Connection,
    chat_id: str,
    target_session: dict[str, str],
    *,
    app_settings: AppSettings,
) -> str:
    return seed_session_memory_now(db, chat_id, target_session, app_settings=app_settings)
