"""SQL primitives for narrative preferences, state, scenes and threads."""

import json
import sqlite3
from collections.abc import Mapping
from typing import Any

from bridge.repository_contracts import require_active_transaction


def load_narrative_settings_row(db: sqlite3.Connection, chat_id: str, session_id: str) -> str | None:
    row = db.execute(
        "SELECT settings_json FROM narrative_settings WHERE chat_id=? AND session_id=?", (chat_id, session_id)
    ).fetchone()
    return str(row[0]) if row else None


def store_narrative_settings_row(
    db: sqlite3.Connection, chat_id: str, session_id: str, settings_json: str, updated_at: float
) -> None:
    require_active_transaction(db)
    db.execute(
        "INSERT INTO narrative_settings(chat_id,session_id,settings_json,updated_at) VALUES(?,?,?,?) "
        "ON CONFLICT(chat_id,session_id) DO UPDATE SET settings_json=excluded.settings_json,"
        "settings_revision=narrative_settings.settings_revision+1,updated_at=excluded.updated_at",
        (chat_id, session_id, settings_json, updated_at),
    )


def load_narrative_default_row(db: sqlite3.Connection, owner_user_id: str) -> str | None:
    row = db.execute("SELECT settings_json FROM narrative_defaults WHERE owner_user_id=?", (owner_user_id,)).fetchone()
    return str(row[0]) if row else None


def store_narrative_default_row(
    db: sqlite3.Connection, owner_user_id: str, settings_json: str, updated_at: float
) -> None:
    require_active_transaction(db)
    db.execute(
        "INSERT INTO narrative_defaults(owner_user_id,settings_json,updated_at) VALUES(?,?,?) "
        "ON CONFLICT(owner_user_id) DO UPDATE SET settings_json=excluded.settings_json,updated_at=excluded.updated_at",
        (owner_user_id, settings_json, updated_at),
    )


def load_narrative_state_row(db: sqlite3.Connection, chat_id: str, session_id: str) -> dict[str, Any] | None:
    cursor = db.execute(
        "SELECT n.active_scene_id,n.active_thread_id,n.story_phase,n.state_revision,n.history_revision,"
        "n.updated_through_rowid,COALESCE(s.viewpoint_character,'') AS viewpoint_character,"
        "COALESCE(s.pov_mode,'') AS pov_mode,s.user_present "
        "FROM narrative_state n LEFT JOIN narrative_scenes s ON s.chat_id=n.chat_id "
        "AND s.session_id=n.session_id AND s.scene_id=n.active_scene_id WHERE n.chat_id=? AND n.session_id=?",
        (chat_id, session_id),
    )
    row = cursor.fetchone()
    if row is None:
        return None
    result = dict(zip((column[0] for column in cursor.description), row, strict=True))
    if result["user_present"] is not None:
        result["user_present"] = bool(result["user_present"])
    return result


def upsert_narrative_state_if_fresh(
    db: sqlite3.Connection,
    chat_id: str,
    session_id: str,
    state_json: str,
    expected_state_revision: int,
    through_rowid: int,
    updated_at: float,
) -> bool:
    require_active_transaction(db)
    state = json.loads(state_json)
    if not isinstance(state, dict):
        raise ValueError("Narrative state must be an object")
    if expected_state_revision < 0 or through_rowid < 0:
        raise ValueError("Narrative revisions cannot be negative")
    values = (
        chat_id,
        session_id,
        state.get("active_scene_id", ""),
        state.get("active_thread_id", ""),
        state.get("story_phase", "setup"),
        through_rowid,
        updated_at,
    )
    current = db.execute(
        "SELECT state_revision FROM narrative_state WHERE chat_id=? AND session_id=?", (chat_id, session_id)
    ).fetchone()
    if current is None:
        if expected_state_revision != 0:
            return False
        cursor = db.execute(
            "INSERT INTO narrative_state(chat_id,session_id,active_scene_id,active_thread_id,story_phase,"
            "updated_through_rowid,updated_at,state_revision) SELECT ?,?,?,?,?,?,?,1 "
            "WHERE EXISTS(SELECT 1 FROM sessions WHERE chat_id=? AND session_id=?)",
            (*values, chat_id, session_id),
        )
    else:
        cursor = db.execute(
            "UPDATE narrative_state SET active_scene_id=?,active_thread_id=?,story_phase=?,"
            "updated_through_rowid=?,updated_at=?,state_revision=state_revision+1,"
            "reconciled_history_revision=history_revision,invalidated_from_rowid=NULL "
            "WHERE chat_id=? AND session_id=? AND state_revision=? "
            "AND (updated_through_rowid<=? OR invalidated_from_rowid IS NOT NULL)",
            (*values[2:], chat_id, session_id, expected_state_revision, through_rowid),
        )
    return cursor.rowcount == 1


def load_narrative_thread(
    db: sqlite3.Connection, chat_id: str, session_id: str, thread_id: str
) -> dict[str, Any] | None:
    row = db.execute(
        "SELECT thread_id,title,status,summary,last_scene_id,source_revision FROM narrative_threads "
        "WHERE chat_id=? AND session_id=? AND thread_id=?",
        (chat_id, session_id, thread_id),
    ).fetchone()
    columns = ("thread_id", "title", "status", "summary", "last_scene_id", "source_revision")
    return dict(zip(columns, row, strict=True)) if row else None


def list_narrative_threads(
    db: sqlite3.Connection, chat_id: str, session_id: str, limit: int = 32, offset: int = 0
) -> list[dict[str, Any]]:
    rows = db.execute(
        "SELECT thread_id,title,status,summary,last_scene_id,source_revision FROM narrative_threads "
        "WHERE chat_id=? AND session_id=? ORDER BY source_revision DESC,thread_id LIMIT ? OFFSET ?",
        (chat_id, session_id, min(100, max(1, limit)), max(0, offset)),
    ).fetchall()
    columns = ("thread_id", "title", "status", "summary", "last_scene_id", "source_revision")
    return [dict(zip(columns, row, strict=True)) for row in rows]


def upsert_narrative_thread(db: sqlite3.Connection, chat_id: str, session_id: str, thread: Mapping[str, Any]) -> bool:
    require_active_transaction(db)
    cursor = db.execute(
        "INSERT INTO narrative_threads(chat_id,session_id,thread_id,title,status,summary,last_scene_id,source_revision)"
        " VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(chat_id,session_id,thread_id) DO UPDATE SET "
        "title=excluded.title,status=excluded.status,summary=excluded.summary,last_scene_id=excluded.last_scene_id,"
        "source_revision=excluded.source_revision WHERE excluded.source_revision>=narrative_threads.source_revision",
        (
            chat_id,
            session_id,
            thread["thread_id"],
            thread["title"],
            thread["status"],
            thread["summary"],
            thread["last_scene_id"],
            thread["source_revision"],
        ),
    )
    return cursor.rowcount == 1


def load_narrative_scene(db: sqlite3.Connection, chat_id: str, session_id: str, scene_id: str) -> dict[str, Any] | None:
    row = db.execute(
        "SELECT scene_id,thread_id,viewpoint_character,pov_mode,user_present,purpose,transition_type,"
        "start_rowid,end_rowid,status,source_revision FROM narrative_scenes "
        "WHERE chat_id=? AND session_id=? AND scene_id=?",
        (chat_id, session_id, scene_id),
    ).fetchone()
    if row is None:
        return None
    present = None if row[4] is None else bool(row[4])
    columns = (
        "scene_id",
        "thread_id",
        "viewpoint_character",
        "pov_mode",
        "user_present",
        "purpose",
        "transition_type",
        "start_rowid",
        "end_rowid",
        "status",
        "source_revision",
    )
    return dict(zip(columns, (*row[:4], present, *row[5:]), strict=True))


def upsert_narrative_scene(db: sqlite3.Connection, chat_id: str, session_id: str, scene: Mapping[str, Any]) -> bool:
    require_active_transaction(db)
    cursor = db.execute(
        "INSERT INTO narrative_scenes(chat_id,session_id,scene_id,thread_id,viewpoint_character,pov_mode,"
        "user_present,purpose,transition_type,start_rowid,end_rowid,status,source_revision) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(chat_id,session_id,scene_id) DO UPDATE SET "
        "thread_id=excluded.thread_id,viewpoint_character=excluded.viewpoint_character,pov_mode=excluded.pov_mode,"
        "user_present=excluded.user_present,purpose=excluded.purpose,transition_type=excluded.transition_type,"
        "start_rowid=excluded.start_rowid,end_rowid=excluded.end_rowid,status=excluded.status,"
        "source_revision=excluded.source_revision WHERE excluded.source_revision>=narrative_scenes.source_revision",
        (
            chat_id,
            session_id,
            scene["scene_id"],
            scene["thread_id"],
            scene["viewpoint_character"],
            scene["pov_mode"],
            scene["user_present"],
            scene["purpose"],
            scene["transition_type"],
            scene["start_rowid"],
            scene["end_rowid"],
            scene["status"],
            scene["source_revision"],
        ),
    )
    return cursor.rowcount == 1


def narrative_settings_revision(db: sqlite3.Connection, chat_id: str, session_id: str) -> int:
    row = db.execute(
        "SELECT settings_revision FROM narrative_settings WHERE chat_id=? AND session_id=?", (chat_id, session_id)
    ).fetchone()
    return int(row[0]) if row else -1


def store_narrative_settings_if_revision(
    db: sqlite3.Connection,
    chat_id: str,
    session_id: str,
    settings_json: str,
    expected_revision: int,
    updated_at: float,
) -> bool:
    require_active_transaction(db)
    if expected_revision == -1:
        cursor = db.execute(
            "INSERT INTO narrative_settings(chat_id,session_id,settings_json,updated_at) "
            "SELECT ?,?,?,? WHERE NOT EXISTS(SELECT 1 FROM narrative_settings WHERE chat_id=? AND session_id=?)",
            (chat_id, session_id, settings_json, updated_at, chat_id, session_id),
        )
    else:
        cursor = db.execute(
            "UPDATE narrative_settings SET settings_json=?,settings_revision=settings_revision+1,updated_at=? "
            "WHERE chat_id=? AND session_id=? AND settings_revision=?",
            (settings_json, updated_at, chat_id, session_id, expected_revision),
        )
    return cursor.rowcount == 1


def load_narrative_clock(db: sqlite3.Connection, chat_id: str, session_id: str) -> dict[str, Any] | None:
    cursor = db.execute(
        "SELECT s.created_at AS session_created_at,COALESCE(n.state_revision,0) AS state_revision,"
        "COALESCE(n.history_revision,0) AS history_revision,COALESCE(n.rewrite_revision,0) AS rewrite_revision,"
        "COALESCE(n.reconciled_history_revision,0) AS reconciled_history_revision,"
        "COALESCE(n.updated_through_rowid,0) AS updated_through_rowid,n.invalidated_from_rowid,"
        "COALESCE(p.settings_revision,-1) AS settings_revision,"
        "(SELECT COALESCE(MAX(id),0) FROM messages m WHERE m.chat_id=s.chat_id "
        "AND m.session_id=s.session_id) AS latest_rowid "
        "FROM sessions s LEFT JOIN narrative_state n ON n.chat_id=s.chat_id AND n.session_id=s.session_id "
        "LEFT JOIN narrative_settings p ON p.chat_id=s.chat_id AND p.session_id=s.session_id "
        "WHERE s.chat_id=? AND s.session_id=?",
        (chat_id, session_id),
    )
    row = cursor.fetchone()
    return dict(zip((column[0] for column in cursor.description), row, strict=True)) if row else None


def next_narrative_transcript_rows(
    db: sqlite3.Connection, chat_id: str, session_id: str, after_rowid: int, through_rowid: int, *, limit: int = 16
) -> list[tuple[int, str, str]]:
    return db.execute(
        "SELECT id,role,content FROM messages WHERE chat_id=? AND session_id=? AND id>? AND id<=? ORDER BY id LIMIT ?",
        (chat_id, session_id, after_rowid, through_rowid, min(32, max(1, limit))),
    ).fetchall()


def delete_narrative_scene(db: sqlite3.Connection, chat_id: str, session_id: str, scene_id: str) -> None:
    require_active_transaction(db)
    db.execute(
        "DELETE FROM narrative_scenes WHERE chat_id=? AND session_id=? AND scene_id=?", (chat_id, session_id, scene_id)
    )


def delete_narrative_thread(db: sqlite3.Connection, chat_id: str, session_id: str, thread_id: str) -> None:
    require_active_transaction(db)
    db.execute(
        "DELETE FROM narrative_threads WHERE chat_id=? AND session_id=? AND thread_id=?",
        (chat_id, session_id, thread_id),
    )


def clear_reconciled_entities(db: sqlite3.Connection, chat_id: str, session_id: str) -> None:
    require_active_transaction(db)
    db.execute("DELETE FROM narrative_scenes WHERE chat_id=? AND session_id=?", (chat_id, session_id))
    db.execute("DELETE FROM narrative_threads WHERE chat_id=? AND session_id=?", (chat_id, session_id))


_NARRATIVE_DERIVED_TABLES = (
    "narrative_state",
    "director_decisions",
    "director_state",
    "ending_goal_history",
    "ending_state",
    "narrative_checkpoints",
    "narrative_arcs",
    "narrative_scenes",
    "narrative_threads",
)


def clear_narrative_story_state(db: sqlite3.Connection, chat_id: str, session_id: str) -> None:
    """Reset derived story state only; keep session style and personal defaults."""
    require_active_transaction(db)
    for table in _NARRATIVE_DERIVED_TABLES:
        db.execute(
            f"DELETE FROM {table} WHERE chat_id=? AND session_id=?",  # noqa: S608 -- fixed internal table names
            (chat_id, session_id),
        )


def narrative_snapshot_rows(
    db: sqlite3.Connection, chat_id: str, session_id: str, through_rowid: int
) -> dict[str, list[dict[str, Any]]]:
    """Read coherent current narrative entities, bounded without silently dropping history."""
    queries = {
        "scenes": (
            "SELECT * FROM narrative_scenes WHERE chat_id=? AND session_id=? "
            "AND source_revision<=? ORDER BY scene_id LIMIT 2049"
        ),
        "threads": (
            "SELECT * FROM narrative_threads WHERE chat_id=? AND session_id=? "
            "AND source_revision<=? ORDER BY thread_id LIMIT 257"
        ),
        "arcs": (
            "SELECT * FROM narrative_arcs WHERE chat_id=? AND session_id=? "
            "AND source_revision<=? ORDER BY arc_id LIMIT 257"
        ),
    }
    limits = {"scenes": 2048, "threads": 256, "arcs": 256}
    result = {}
    for name, query in queries.items():
        cursor = db.execute(query, (chat_id, session_id, through_rowid))
        try:
            columns = [item[0] for item in cursor.description]
            rows = [dict(zip(columns, row, strict=True)) for row in cursor]
        finally:
            cursor.close()
        if len(rows) > limits[name]:
            raise ValueError("Narrative history is too large for a bounded pre-finale snapshot")
        result[name] = rows
    return result


def close_reconciled_narrative_state(
    db: sqlite3.Connection, chat_id: str, session_id: str, *, expected_revision: int, epilogue_rowid: int
) -> bool:
    require_active_transaction(db)
    return (
        db.execute(
            "UPDATE narrative_state SET story_phase='closed',state_revision=state_revision+1 "
            "WHERE chat_id=? AND session_id=? AND state_revision=? AND updated_through_rowid>=? "
            "AND reconciled_history_revision=history_revision AND invalidated_from_rowid IS NULL",
            (chat_id, session_id, expected_revision, epilogue_rowid),
        ).rowcount
        == 1
    )
