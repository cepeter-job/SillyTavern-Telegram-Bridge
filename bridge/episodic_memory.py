"""Session-scoped durable episodic memory records.

This is intentionally separate from continuity summaries. Summaries answer
"what is happening now"; episodic records preserve durable events that may be
recalled later.
"""

from __future__ import annotations

import json
import sqlite3
import time

from bridge.limits import EPISODIC_CONTEXT_MAX_CHARS


def _normalized_summary(value: str) -> str:
    return " ".join(str(value or "").split()).casefold()


def _normalize_known_by(values) -> tuple[str, ...]:
    if not isinstance(values, (list, tuple)):
        return ()
    result = []
    seen = set()
    for value in values:
        name = " ".join(str(value or "").split()).strip()
        folded = name.casefold()
        if not name or folded in seen:
            continue
        seen.add(folded)
        result.append(name)
    return tuple(result)


def _decode_known_by(value: str) -> tuple[str, ...]:
    try:
        decoded = json.loads(str(value or "[]"))
    except (TypeError, ValueError):
        return ()
    return _normalize_known_by(decoded)


def _visibility_values(visibility: str, known_by) -> tuple[str, tuple[str, ...]]:
    mode = str(visibility or "shared").strip().casefold()
    if mode not in {"shared", "restricted"}:
        raise ValueError("episodic visibility must be shared or restricted")
    names = _normalize_known_by(known_by)
    if mode == "restricted" and not names:
        raise ValueError("restricted episodic memory requires known_by")
    return mode, names


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
    visibility: str = "shared",
    known_by: tuple[str, ...] | list[str] = (),
) -> bool:
    mode, names = _visibility_values(visibility, known_by)
    normalized = _normalized_summary(summary)
    existing = db.execute(
        """
        SELECT m.memory_id,m.summary,COALESCE(v.visibility,'shared'),COALESCE(v.known_by_json,'[]'),
               m.source_start_rowid,m.source_end_rowid
        FROM episodic_memories AS m
        LEFT JOIN episodic_memory_visibility AS v ON v.memory_id=m.memory_id
        WHERE m.chat_id=? AND m.session_id=? AND m.kind=?
        """,
        (chat_id, session_id, kind),
    ).fetchall()
    for _memory_id, existing_summary, existing_visibility, existing_known_by, start, end in existing:
        if (
            _normalized_summary(existing_summary) == normalized
            and (start, end) == (source_start_rowid, source_end_rowid)
            and existing_visibility == mode
            and {name.casefold() for name in _decode_known_by(existing_known_by)} == {name.casefold() for name in names}
        ):
            return False
    cursor = db.execute(
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
    memory_id = int(cursor.lastrowid)
    db.execute(
        """
        INSERT INTO episodic_memory_visibility(memory_id,chat_id,session_id,visibility,known_by_json)
        VALUES(?,?,?,?,?)
        """,
        (memory_id, chat_id, session_id, mode, json.dumps(names, ensure_ascii=False)),
    )
    return True


def invalidate_episodic_memories_from_row(db: sqlite3.Connection, chat_id: str, session_id: str, rowid: int) -> int:
    rows = db.execute(
        """
        SELECT memory_id FROM episodic_memories
        WHERE chat_id=? AND session_id=? AND source_end_rowid>=?
        """,
        (chat_id, session_id, int(rowid)),
    ).fetchall()
    memory_ids = [int(item[0]) for item in rows]
    if not memory_ids:
        return 0
    db.execute(
        """
        DELETE FROM episodic_memory_visibility
        WHERE memory_id IN (
            SELECT memory_id FROM episodic_memories
            WHERE chat_id=? AND session_id=? AND source_end_rowid>=?
        )
        """,
        (chat_id, session_id, int(rowid)),
    )
    db.execute(
        """
        DELETE FROM episodic_memories
        WHERE chat_id=? AND session_id=? AND source_end_rowid>=?
        """,
        (chat_id, session_id, int(rowid)),
    )
    return len(memory_ids)


def purge_episodic_memories(db: sqlite3.Connection, chat_id: str, session_id: str) -> int:
    rows = db.execute(
        "SELECT memory_id FROM episodic_memories WHERE chat_id=? AND session_id=?",
        (chat_id, session_id),
    ).fetchall()
    memory_ids = [int(item[0]) for item in rows]
    db.execute(
        "DELETE FROM episodic_memory_visibility WHERE chat_id=? AND session_id=?",
        (chat_id, session_id),
    )
    db.execute(
        "DELETE FROM episodic_memories WHERE chat_id=? AND session_id=?",
        (chat_id, session_id),
    )
    return len(memory_ids)


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
    from bridge.memory_scope_store import read_episodic_block, resolve_memory_scope

    scope = resolve_memory_scope(db, chat_id, session, _fields)
    return read_episodic_block(db, scope, query, limit=limit, max_chars=max_chars).text if scope else ""
