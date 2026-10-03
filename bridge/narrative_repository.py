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
            "updated_through_rowid=?,updated_at=?,state_revision=state_revision+1 "
            "WHERE chat_id=? AND session_id=? AND state_revision=? AND updated_through_rowid<=?",
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
