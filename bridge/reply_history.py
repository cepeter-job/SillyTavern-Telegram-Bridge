"""Materialize the bounded story-history window inside its timing boundary."""

import sqlite3
from contextlib import closing

from bridge.context_compaction import context_history_candidate_limit
from bridge.performance import perf_span
from bridge.settings import AppSettings


def load_history_rows(db: sqlite3.Connection, chat_id: str, session_id: str, *, app_settings: AppSettings) -> list:
    with (
        perf_span("history_load", app_settings=app_settings),
        closing(
            db.execute(
                "SELECT role, content FROM messages WHERE chat_id=? AND session_id=? "
                "ORDER BY created_at DESC, rowid DESC LIMIT ?",
                (chat_id, session_id, context_history_candidate_limit(app_settings=app_settings)),
            )
        ) as rows,
    ):
        return list(reversed(rows.fetchall()))
