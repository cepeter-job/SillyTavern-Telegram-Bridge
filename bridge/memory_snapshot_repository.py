"""Checkpoint-time local memory capture; external Hindsight documents are not copied."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from bridge.repository_contracts import require_active_transaction


def snapshot_local_memory(db: sqlite3.Connection, chat_id: str, session_id: str, through_rowid: int) -> dict[str, Any]:
    summary = db.execute(
        "SELECT substr(summary,1,1048577),covered_until_rowid,updated_at FROM session_summaries "
        "WHERE chat_id=? AND session_id=? AND covered_until_rowid<=?",
        (chat_id, session_id, through_rowid),
    ).fetchone()
    cursor = db.execute(
        "SELECT e.memory_id,e.kind,e.importance,substr(e.summary,1,1048577) AS summary,e.source_start_rowid,"
        "e.source_end_rowid,e.created_at,COALESCE(v.visibility,'shared') AS visibility,"
        "COALESCE(v.known_by_json,'[]') AS known_by_json FROM episodic_memories e "
        "LEFT JOIN episodic_memory_visibility v ON v.memory_id=e.memory_id "
        "JOIN memory_fact_provenance p ON p.memory_id=e.memory_id AND p.valid=1 "
        "WHERE e.chat_id=? AND e.session_id=? AND e.source_end_rowid<=? ORDER BY e.memory_id LIMIT 513",
        (chat_id, session_id, through_rowid),
    )
    episodes = []
    budget = len(str(summary[0])) if summary else 0
    try:
        columns = [item[0] for item in cursor.description]
        for row in cursor:
            budget += len(json.dumps(row, ensure_ascii=False))
            if budget > 1048576:
                raise ValueError("Local memory is too large for a bounded pre-finale snapshot")
            episodes.append(dict(zip(columns, row, strict=True)))
            if len(episodes) > 512:
                raise ValueError("Too many local memories for a bounded pre-finale snapshot")
    finally:
        cursor.close()
    if budget > 1048576:
        raise ValueError("Local summary exceeds the pre-finale snapshot bound")
    return {
        "summary": dict(zip(("summary", "covered_until_rowid", "updated_at"), summary, strict=True))
        if summary
        else None,
        "episodic": episodes,
    }


def restore_local_memory_snapshot(db: sqlite3.Connection, chat_id: str, session_id: str, memory: dict) -> None:
    """Restore already-remapped, checkpoint-time local memories with fresh identities."""
    require_active_transaction(db)
    summary = memory.get("summary")
    if summary:
        db.execute(
            "INSERT INTO session_summaries VALUES(?,?,?,?,?)",
            (chat_id, session_id, summary["summary"], summary["covered_until_rowid"], summary["updated_at"]),
        )
    for item in memory.get("episodic", []):
        row = db.execute(
            "INSERT INTO episodic_memories(chat_id,session_id,kind,importance,summary,source_start_rowid,"
            "source_end_rowid,created_at) VALUES(?,?,?,?,?,?,?,?)",
            (
                chat_id,
                session_id,
                item["kind"],
                item["importance"],
                item["summary"],
                item["source_start_rowid"],
                item["source_end_rowid"],
                item["created_at"],
            ),
        )
        db.execute(
            "INSERT INTO episodic_memory_visibility VALUES(?,?,?,?,?)",
            (row.lastrowid, chat_id, session_id, item["visibility"], item["known_by_json"]),
        )
