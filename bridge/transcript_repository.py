"""Canonical transcript repository owner."""

from __future__ import annotations

import sqlite3


def committed_assistant_for_message(
    db: sqlite3.Connection, chat_id: str, telegram_message_id: int | str
) -> tuple[int, str, str | None] | None:
    return db.execute(
        """SELECT assistant.rowid, assistant.content, assistant.telegram_message_ids
        FROM messages AS user_message
        JOIN messages AS assistant
          ON assistant.chat_id=user_message.chat_id
         AND assistant.session_id=user_message.session_id
         AND assistant.role='assistant'
         AND assistant.rowid > user_message.rowid
        WHERE user_message.chat_id=? AND user_message.role='user' AND user_message.telegram_message_id=?
        AND NOT EXISTS (SELECT 1 FROM messages intervening WHERE intervening.chat_id=user_message.chat_id
            AND intervening.session_id=user_message.session_id AND intervening.role='user'
            AND intervening.rowid>user_message.rowid AND intervening.rowid<assistant.rowid)
        ORDER BY assistant.rowid LIMIT 1""",
        (chat_id, str(telegram_message_id)),
    ).fetchone()


def native_edit_target(db: sqlite3.Connection, chat_id: str, message_id: int) -> tuple[int, str, str] | None:
    """Read the original message's session without changing the active session."""
    return db.execute(
        "SELECT rowid,session_id,role FROM messages WHERE chat_id=? AND "
        "telegram_message_id=? ORDER BY rowid DESC LIMIT 1",
        (chat_id, str(message_id)),
    ).fetchone()


def assistant_by_row(db: sqlite3.Connection, rowid: int, chat_id: str, session_id: str) -> tuple[int, str] | None:
    return db.execute(
        "SELECT rowid,content FROM messages WHERE rowid=? AND chat_id=? AND session_id=? AND role='assistant'",
        (rowid, chat_id, session_id),
    ).fetchone()


def recent_transcript_rows(
    db: sqlite3.Connection,
    chat_id: str,
    session_id: str,
    *,
    limit: int,
    through_rowid: int | None = None,
) -> list[tuple[int, str, str]]:
    """Read the latest bounded window in chronological order without owning a transaction."""
    if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
        raise ValueError("Transcript row limit must be a positive integer")
    cutoff = int(through_rowid) if through_rowid is not None else None
    rows = db.execute(
        "SELECT rowid,role,content FROM messages WHERE chat_id=? AND session_id=? "
        "AND (? IS NULL OR rowid<=?) ORDER BY created_at DESC,rowid DESC LIMIT ?",
        (str(chat_id), str(session_id), cutoff, cutoff, limit),
    ).fetchall()
    return list(reversed(rows))
