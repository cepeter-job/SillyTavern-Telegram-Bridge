"""SQL-only branch lineage and guarded activation of an independent target session."""

from __future__ import annotations

import sqlite3
from typing import Any

from bridge.repository_contracts import require_active_transaction


def branch_for_operation(db: sqlite3.Connection, operation_id: str) -> dict[str, Any] | None:
    cursor = db.execute("SELECT * FROM narrative_branches WHERE operation_id=?", (operation_id,))
    try:
        row = cursor.fetchone()
        return dict(zip((c[0] for c in cursor.description), row, strict=True)) if row else None
    finally:
        cursor.close()


def record_branch(
    db: sqlite3.Connection,
    chat_id: str,
    target: str,
    origin: str,
    checkpoint: str,
    revision: int,
    operation_id: str,
    now: float,
) -> None:
    require_active_transaction(db)
    db.execute(
        "INSERT INTO narrative_branches(chat_id,target_session_id,origin_session_id,checkpoint_id,"
        "checkpoint_revision,operation_id,created_at) VALUES(?,?,?,?,?,?,?)",
        (chat_id, target, origin, checkpoint, revision, operation_id, now),
    )


def finish_branch_memory(db: sqlite3.Connection, operation_id: str, status: str) -> None:
    require_active_transaction(db)
    if status not in {"disabled", "ready", "degraded"}:
        raise ValueError("Invalid branch memory result")
    db.execute(
        "UPDATE narrative_branches SET memory_status=? WHERE operation_id=? AND memory_status='pending'",
        (status, operation_id),
    )


def activate_branch_if_origin(db: sqlite3.Connection, chat_id: str, origin: str, target: str) -> bool:
    require_active_transaction(db)
    return (
        db.execute(
            "UPDATE meta SET value=? WHERE key=? AND value=?", (target, f"active_session:{chat_id}", origin)
        ).rowcount
        == 1
    )


def linked_alternate_checkpoint(db: sqlite3.Connection, chat_id: str, session_id: str) -> str | None:
    """Cheap UI eligibility; the creation service verifies the complete immutable snapshot."""
    row = db.execute(
        "SELECT c.checkpoint_id FROM ending_state e JOIN narrative_checkpoints c ON c.checkpoint_id=e.checkpoint_id "
        "AND c.chat_id=e.chat_id AND c.session_id=e.session_id WHERE e.chat_id=? AND e.session_id=? "
        "AND e.lifecycle='closed' AND c.kind='pre_finale' AND c.format_version=1",
        (chat_id, session_id),
    ).fetchone()
    return str(row[0]) if row else None
