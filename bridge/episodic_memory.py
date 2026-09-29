"""Session-scoped durable episodic memory records.

This is intentionally separate from continuity summaries. Summaries answer
"what is happening now"; episodic records preserve durable events that may be
recalled later.
"""

from __future__ import annotations

import re
import sqlite3
import time
from dataclasses import dataclass

from bridge.limits import EPISODIC_CONTEXT_MAX_CHARS


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


_QUERY_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "at",
        "for",
        "from",
        "has",
        "have",
        "how",
        "in",
        "into",
        "is",
        "it",
        "its",
        "of",
        "on",
        "that",
        "the",
        "this",
        "to",
        "was",
        "were",
        "what",
        "when",
        "where",
        "who",
        "why",
        "with",
        "you",
        "your",
    }
)


def _relevance_terms(value: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[A-Za-z0-9_'-]+", str(value or "").casefold(), flags=re.UNICODE)
        if len(token) >= 2 and token not in _QUERY_STOPWORDS
    }


def episodic_context_for_prompt(
    db: sqlite3.Connection,
    chat_id: str,
    session: dict[str, str],
    _fields: dict[str, str],
    query: str,
    *,
    limit: int = 6,
    max_chars: int = EPISODIC_CONTEXT_MAX_CHARS,
) -> str:
    query_terms = _relevance_terms(query)
    if not query_terms or limit <= 0 or max_chars <= 0:
        return ""
    rows = db.execute(
        """
        SELECT memory_id,kind,importance,summary,source_end_rowid
        FROM episodic_memories
        WHERE chat_id=? AND session_id=?
        ORDER BY memory_id DESC
        LIMIT 200
        """,
        (chat_id, session["session_id"]),
    ).fetchall()
    ranked = []
    for memory_id, kind, importance, summary, source_end_rowid in rows:
        overlap = len(query_terms & _relevance_terms(summary))
        if overlap <= 0:
            continue
        ranked.append(
            (
                overlap,
                float(importance),
                int(source_end_rowid),
                int(memory_id),
                str(kind),
                str(summary),
            )
        )
    ranked.sort(key=lambda item: item[:4], reverse=True)
    lines: list[str] = []
    length = 0
    for _overlap, _importance, _rowid, _memory_id, kind, summary in ranked[:limit]:
        line = f"[{kind}] {summary.strip()}"
        projected = length + len(line) + (1 if lines else 0)
        if projected > max_chars:
            break
        lines.append(line)
        length = projected
    return "\n".join(lines)
