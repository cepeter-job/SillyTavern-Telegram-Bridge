"""Immutable checkpoint storage; all mutations belong to the caller transaction."""

from __future__ import annotations

import sqlite3
from typing import Any

from bridge.repository_contracts import require_active_transaction


def _checkpoint(row: tuple | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return dict(
        zip(
            ("checkpoint_id", "kind", "source_revision", "through_rowid", "format_version", "payload_json"),
            row,
            strict=True,
        )
    )


def load_rolling_checkpoint_before(
    db: sqlite3.Connection, chat_id: str, session_id: str, before_rowid: int
) -> dict[str, Any] | None:
    return _checkpoint(
        db.execute(
            "SELECT checkpoint_id,kind,source_revision,through_rowid,format_version,payload_json "
            "FROM narrative_checkpoints WHERE chat_id=? AND session_id=? AND kind='reconciliation' "
            "AND through_rowid<? ORDER BY source_revision DESC LIMIT 1",
            (chat_id, session_id, before_rowid),
        ).fetchone()
    )


def rolling_checkpoints_after(
    db: sqlite3.Connection, chat_id: str, session_id: str, source_revision: int
) -> list[dict[str, Any]]:
    rows = db.execute(
        "SELECT checkpoint_id,kind,source_revision,through_rowid,format_version,payload_json "
        "FROM narrative_checkpoints WHERE chat_id=? AND session_id=? AND kind='reconciliation' "
        "AND source_revision>? ORDER BY source_revision DESC LIMIT 32",
        (chat_id, session_id, source_revision),
    ).fetchall()
    return [record for row in rows if (record := _checkpoint(row)) is not None]


def insert_narrative_checkpoint(
    db: sqlite3.Connection,
    chat_id: str,
    session_id: str,
    checkpoint_id: str,
    *,
    kind: str,
    source_revision: int,
    through_rowid: int,
    payload_json: str,
    created_at: float,
) -> None:
    require_active_transaction(db)
    db.execute(
        "INSERT INTO narrative_checkpoints(checkpoint_id,chat_id,session_id,kind,source_revision,"
        "through_rowid,format_version,payload_json,created_at) VALUES(?,?,?,?,?,?,1,?,?)",
        (checkpoint_id, chat_id, session_id, kind, source_revision, through_rowid, payload_json, created_at),
    )
    if kind == "reconciliation":
        db.execute(
            "DELETE FROM narrative_checkpoints WHERE checkpoint_id IN (SELECT checkpoint_id "
            "FROM narrative_checkpoints WHERE chat_id=? AND session_id=? AND kind='reconciliation' "
            "ORDER BY source_revision DESC LIMIT -1 OFFSET 32)",
            (chat_id, session_id),
        )


def delete_rolling_checkpoints_after(
    db: sqlite3.Connection, chat_id: str, session_id: str, source_revision: int
) -> None:
    require_active_transaction(db)
    db.execute(
        "DELETE FROM narrative_checkpoints WHERE chat_id=? AND session_id=? AND kind='reconciliation' "
        "AND source_revision>?",
        (chat_id, session_id, source_revision),
    )


def load_pre_finale_checkpoint_row(
    db: sqlite3.Connection, chat_id: str, session_id: str, checkpoint_id: str
) -> dict[str, Any] | None:
    return _checkpoint(
        db.execute(
            "SELECT checkpoint_id,kind,source_revision,through_rowid,format_version,payload_json "
            "FROM narrative_checkpoints WHERE chat_id=? AND session_id=? AND kind='pre_finale' AND checkpoint_id=?",
            (chat_id, session_id, checkpoint_id),
        ).fetchone()
    )
