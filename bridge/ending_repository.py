"""Caller-owned SQL primitives for ending lifecycle and bounded goal history."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from typing import Any

from bridge.repository_contracts import require_active_transaction

_COLUMNS = (
    "lifecycle",
    "current_goal",
    "goal_revision",
    "finale_ready_revision",
    "finale_direction_revision",
    "checkpoint_id",
    "finale_operation_id",
    "finale_committed_rowid",
    "resolution_rowid",
    "epilogue_operation_id",
    "epilogue_committed_rowid",
    "updated_at",
    "ready_history_revision",
    "ready_settings_revision",
    "ready_rewrite_revision",
    "ready_through_rowid",
    "readiness_reason",
    "required_arcs_json",
    "resolution_evidence_json",
    "epilogue_brief_json",
    "work_token",
    "work_started_at",
    "work_stage",
    "last_error",
    "last_attempt_at",
)
_SELECT = (
    "SELECT lifecycle_revision,lifecycle,current_goal,goal_revision,finale_ready_revision,"
    "finale_direction_revision,checkpoint_id,finale_operation_id,finale_committed_rowid,resolution_rowid,"
    "epilogue_operation_id,epilogue_committed_rowid,updated_at,ready_history_revision,"
    "ready_settings_revision,ready_rewrite_revision,ready_through_rowid,readiness_reason,"
    "required_arcs_json,resolution_evidence_json,epilogue_brief_json,work_token,work_started_at,"
    "work_stage,last_error,last_attempt_at"
    " FROM ending_state WHERE chat_id=? AND session_id=?"
)
_UPDATE = (
    "UPDATE ending_state SET lifecycle_revision=lifecycle_revision+1,lifecycle=?,current_goal=?,"
    "goal_revision=?,finale_ready_revision=?,finale_direction_revision=?,checkpoint_id=?,"
    "finale_operation_id=?,finale_committed_rowid=?,resolution_rowid=?,epilogue_operation_id=?,"
    "epilogue_committed_rowid=?,updated_at=?,ready_history_revision=?,ready_settings_revision=?,"
    "ready_rewrite_revision=?,ready_through_rowid=?,readiness_reason=?,required_arcs_json=?,"
    "resolution_evidence_json=?,epilogue_brief_json=?,work_token=?,work_started_at=?,work_stage=?,"
    "last_error=?,last_attempt_at=?"
    " WHERE chat_id=? AND session_id=? AND lifecycle_revision=?"
)
_GOAL_COLUMNS = (
    "goal_revision",
    "source",
    "story_revision",
    "scene_id",
    "previous_goal",
    "new_goal",
    "reason",
    "created_at",
)


def load_ending_row(db: sqlite3.Connection, chat_id: str, session_id: str) -> dict[str, Any] | None:
    row = db.execute(_SELECT, (chat_id, session_id)).fetchone()
    if row is None:
        return None
    value = dict(zip(("lifecycle_revision", *_COLUMNS), row, strict=True))
    value["required_arcs"] = tuple(json.loads(value.pop("required_arcs_json")))
    return value


def save_ending_row(
    db: sqlite3.Connection, chat_id: str, session_id: str, values: Mapping[str, Any], *, expected_revision: int
) -> bool:
    require_active_transaction(db)
    data = dict(values)
    data["required_arcs_json"] = json.dumps(data.pop("required_arcs"), ensure_ascii=False, separators=(",", ":"))
    if expected_revision == 0:
        db.execute(
            "INSERT INTO ending_state(chat_id,session_id) VALUES(?,?) ON CONFLICT(chat_id,session_id) DO NOTHING",
            (chat_id, session_id),
        )
    cursor = db.execute(_UPDATE, (*[data[name] for name in _COLUMNS], chat_id, session_id, expected_revision))
    return cursor.rowcount == 1


def append_ending_goal_revision(
    db: sqlite3.Connection, chat_id: str, session_id: str, values: Mapping[str, Any]
) -> None:
    require_active_transaction(db)
    db.execute(
        "INSERT INTO ending_goal_history(chat_id,session_id,goal_revision,source,story_revision,scene_id,"
        "previous_goal,new_goal,reason,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (chat_id, session_id, *[values[name] for name in _GOAL_COLUMNS]),
    )


def list_ending_goal_revisions(
    db: sqlite3.Connection, chat_id: str, session_id: str, *, limit: int = 100
) -> list[dict[str, Any]]:
    rows = db.execute(
        "SELECT goal_revision,source,story_revision,scene_id,previous_goal,new_goal,reason,created_at "
        "FROM ending_goal_history WHERE chat_id=? AND session_id=? ORDER BY goal_revision DESC LIMIT ?",
        (chat_id, session_id, min(100, max(1, limit))),
    ).fetchall()
    return [dict(zip(_GOAL_COLUMNS, row, strict=True)) for row in rows]
