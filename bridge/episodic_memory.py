"""Session-scoped durable episodic memory records.

This is intentionally separate from continuity summaries. Summaries answer
"what is happening now"; episodic records preserve durable events that may be
recalled later.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class EpisodicMemory:
    memory_id: int
    kind: str
    importance: float
    summary: str
    source_start_rowid: int
    source_end_rowid: int


def list_episodic_memories(
    db: sqlite3.Connection, chat_id: str, session_id: str, limit: int = 10
) -> list[EpisodicMemory]:
    rows = db.execute(
        """
        SELECT memory_id,kind,importance,summary,source_start_rowid,source_end_rowid
        FROM episodic_memories
        WHERE chat_id=? AND session_id=?
        ORDER BY importance DESC, memory_id DESC
        LIMIT ?
        """,
        (chat_id, session_id, limit),
    ).fetchall()
    return [EpisodicMemory(*row) for row in rows]


def _normalized_summary(value: str) -> str:
    return " ".join(str(value or "").split()).casefold()


def store_episodic_memory(
    db: sqlite3.Connection,
    chat_id: str,
    session_id: str,
    *,
    kind: str,
    importance: float,
    summary: str,
    source_start_rowid: int,
    source_end_rowid: int,
) -> bool:
    normalized = _normalized_summary(summary)
    existing = db.execute(
        "SELECT summary FROM episodic_memories WHERE chat_id=? AND session_id=? AND kind=?",
        (chat_id, session_id, kind),
    ).fetchall()
    if any(_normalized_summary(row[0]) == normalized for row in existing):
        return False
    db.execute(
        """
        INSERT INTO episodic_memories(
            chat_id,session_id,kind,importance,summary,
            source_start_rowid,source_end_rowid,created_at
        ) VALUES(?,?,?,?,?,?,?,?)
        """,
        (
            chat_id,
            session_id,
            kind,
            float(importance),
            summary.strip(),
            int(source_start_rowid),
            int(source_end_rowid),
            time.time(),
        ),
    )
    return True
