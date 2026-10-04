"""Session-owned SQL primitives for committed arc state and its source evidence."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from typing import Any

from bridge.repository_contracts import require_active_transaction

_COLUMNS = (
    "arc_id",
    "title",
    "status",
    "phase",
    "importance",
    "summary",
    "open_questions",
    "related_threads",
    "source_revision",
    "evidence",
)


def _decode(row: tuple) -> dict[str, Any]:
    data = dict(zip(_COLUMNS, row, strict=True))
    for key in ("open_questions", "related_threads", "evidence"):
        data[key] = json.loads(data[key])
    return data


def load_arc_row(db: sqlite3.Connection, chat_id: str, session_id: str, arc_id: str) -> dict[str, Any] | None:
    row = db.execute(
        "SELECT arc_id,title,status,phase,importance,summary,open_questions_json,related_threads_json,"
        "source_revision,evidence_json FROM narrative_arcs WHERE chat_id=? AND session_id=? AND arc_id=?",
        (chat_id, session_id, arc_id),
    ).fetchone()
    return _decode(row) if row else None


def list_arc_rows(
    db: sqlite3.Connection, chat_id: str, session_id: str, *, limit: int = 32, offset: int = 0
) -> list[dict[str, Any]]:
    rows = db.execute(
        "SELECT arc_id,title,status,phase,importance,summary,open_questions_json,related_threads_json,"
        "source_revision,evidence_json FROM narrative_arcs WHERE chat_id=? AND session_id=? "
        "ORDER BY source_revision DESC,arc_id LIMIT ? OFFSET ?",
        (chat_id, session_id, min(128, max(1, limit)), max(0, offset)),
    ).fetchall()
    return [_decode(row) for row in rows]


def store_arc_row(db: sqlite3.Connection, chat_id: str, session_id: str, arc: Mapping[str, Any]) -> bool:
    require_active_transaction(db)
    cursor = db.execute(
        "INSERT INTO narrative_arcs(chat_id,session_id,arc_id,title,status,phase,importance,summary,"
        "open_questions_json,related_threads_json,source_revision,evidence_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(chat_id,session_id,arc_id) DO UPDATE SET title=excluded.title,status=excluded.status,"
        "phase=excluded.phase,importance=excluded.importance,summary=excluded.summary,"
        "open_questions_json=excluded.open_questions_json,related_threads_json=excluded.related_threads_json,"
        "source_revision=excluded.source_revision,evidence_json=excluded.evidence_json "
        "WHERE excluded.source_revision>=narrative_arcs.source_revision",
        (
            chat_id,
            session_id,
            arc["arc_id"],
            arc["title"],
            arc["status"],
            arc["phase"],
            arc["importance"],
            arc["summary"],
            json.dumps(arc["open_questions"], ensure_ascii=False, separators=(",", ":")),
            json.dumps(arc["related_threads"], ensure_ascii=False, separators=(",", ":")),
            arc["source_revision"],
            json.dumps(arc["evidence"], ensure_ascii=False, separators=(",", ":")),
        ),
    )
    return cursor.rowcount == 1


def delete_arc_row(db: sqlite3.Connection, chat_id: str, session_id: str, arc_id: str) -> None:
    require_active_transaction(db)
    db.execute(
        "DELETE FROM narrative_arcs WHERE chat_id=? AND session_id=? AND arc_id=?", (chat_id, session_id, arc_id)
    )


def clear_arc_rows(db: sqlite3.Connection, chat_id: str, session_id: str) -> None:
    require_active_transaction(db)
    db.execute("DELETE FROM narrative_arcs WHERE chat_id=? AND session_id=?", (chat_id, session_id))
