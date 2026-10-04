"""SQL primitives for accepted planning state; callers own all transactions."""

from __future__ import annotations

import sqlite3
from typing import Any

from bridge.repository_contracts import require_active_transaction

_EMPTY: dict[str, Any] = {
    "goal": "",
    "active_direction": "",
    "direction_scope": "next_scene",
    "state_revision": 0,
    "accepted_through_rowid": 0,
    "last_director_turn": 0,
    "last_director_run": 0,
    "degraded_state": "",
    "updated_at": 0,
    "active_proposal_json": "{}",
    "accepted_rewrite_revision": 0,
    "accepted_settings_revision": 0,
    "accepted_scene_id": "",
    "direction_source": "ai",
    "direction_until_turn": 0,
    "inflight_token": "",
    "inflight_started_at": 0,
    "last_attempt_at": 0,
    "last_attempt_turn": 0,
    "last_event_key": "",
}


def load_director_state(db: sqlite3.Connection, chat_id: str, session_id: str) -> dict[str, Any]:
    cursor = db.execute("SELECT * FROM director_state WHERE chat_id=? AND session_id=?", (chat_id, session_id))
    row = cursor.fetchone()
    return dict(zip((c[0] for c in cursor.description), row, strict=True)) if row else dict(_EMPTY)


def director_ending_lifecycle(db: sqlite3.Connection, chat_id: str, session_id: str) -> str:
    row = db.execute(
        "SELECT lifecycle FROM ending_state WHERE chat_id=? AND session_id=?", (chat_id, session_id)
    ).fetchone()
    return str(row[0]) if row else "open"


def director_story_turns(
    db: sqlite3.Connection, chat_id: str, session_id: str, through_rowid: int | None = None
) -> int:
    row = db.execute(
        "SELECT COUNT(*) FROM messages WHERE chat_id=? AND session_id=? AND role='assistant' AND (? IS NULL OR id<=?)",
        (chat_id, session_id, through_rowid, through_rowid),
    ).fetchone()
    return int(row[0])


def director_recent_story(db: sqlite3.Connection, chat_id: str, session_id: str, through_rowid: int) -> list[tuple]:
    return list(
        reversed(
            db.execute(
                "SELECT id,role,substr(content,1,2000) FROM messages WHERE chat_id=? AND session_id=? AND id<=? "
                "ORDER BY id DESC LIMIT 8",
                (chat_id, session_id, through_rowid),
            ).fetchall()
        )
    )


def director_npc_names(db: sqlite3.Connection, chat_id: str, session_id: str, through_rowid: int) -> set[str]:
    return {
        str(r[0])
        for r in db.execute(
            "SELECT display_name FROM npc_entities WHERE chat_id=? AND session_id=? AND first_seen_rowid<=? "
            "ORDER BY last_seen_rowid DESC LIMIT 64",
            (chat_id, session_id, through_rowid),
        )
    }


def director_group_files(db: sqlite3.Connection, chat_id: str, session_id: str) -> str:
    row = db.execute(
        "SELECT substr(members_json,1,8192) FROM group_sessions WHERE chat_id=? AND session_id=?", (chat_id, session_id)
    ).fetchone()
    return str(row[0]) if row else "[]"


def claim_director_run(
    db: sqlite3.Connection, chat_id: str, session_id: str, token: str, now: float, *, lease: float
) -> bool:
    require_active_transaction(db)
    cursor = db.execute(
        "INSERT INTO director_state(chat_id,session_id,inflight_token,inflight_started_at) VALUES(?,?,?,?) "
        "ON CONFLICT(chat_id,session_id) DO UPDATE SET inflight_token=excluded.inflight_token,"
        "inflight_started_at=excluded.inflight_started_at WHERE director_state.inflight_token='' "
        "OR director_state.inflight_started_at<=?",
        (chat_id, session_id, token, now, now - lease),
    )
    return cursor.rowcount == 1


def release_director_run(db: sqlite3.Connection, chat_id: str, session_id: str, token: str) -> None:
    require_active_transaction(db)
    db.execute(
        "UPDATE director_state SET inflight_token='',inflight_started_at=0 "
        "WHERE chat_id=? AND session_id=? AND inflight_token=?",
        (chat_id, session_id, token),
    )


def append_director_decision(
    db: sqlite3.Connection,
    chat_id: str,
    session_id: str,
    *,
    source: str,
    result: str,
    expected_revision: int,
    proposal_json: str,
    accepted_direction: str,
    reason: str,
    created_at: float,
) -> int:
    require_active_transaction(db)
    cursor = db.execute(
        "INSERT INTO director_decisions(chat_id,session_id,source,result,expected_revision,proposal_json,"
        "accepted_direction,reason,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
        (chat_id, session_id, source, result, expected_revision, proposal_json, accepted_direction, reason, created_at),
    )
    return int(cursor.lastrowid or 0)


def director_decision_history(
    db: sqlite3.Connection, chat_id: str, session_id: str, limit: int = 20
) -> list[dict[str, Any]]:
    cursor = db.execute(
        "SELECT decision_id,source,result,expected_revision,accepted_direction,reason,created_at "
        "FROM director_decisions WHERE chat_id=? AND session_id=? ORDER BY decision_id DESC LIMIT ?",
        (chat_id, session_id, max(1, min(200, limit))),
    )
    keys = [c[0] for c in cursor.description]
    return [dict(zip(keys, row, strict=True)) for row in cursor]


def publish_director_plan(
    db: sqlite3.Connection,
    chat_id: str,
    session_id: str,
    *,
    token: str,
    expected_director_revision: int,
    proposal_json: str,
    direction: str,
    through_rowid: int,
    rewrite_revision: int,
    settings_revision: int,
    scene_id: str,
    story_turn: int,
    until_turn: int,
    event_key: str,
    now: float,
) -> bool:
    require_active_transaction(db)
    cursor = db.execute(
        "UPDATE director_state SET active_direction=?,active_proposal_json=?,direction_source='ai',"
        "direction_scope='next_scene',accepted_through_rowid=?,accepted_rewrite_revision=?,accepted_settings_revision=?,"
        "accepted_scene_id=?,last_director_turn=?,direction_until_turn=?,last_director_run=?,last_attempt_at=?,"
        "last_attempt_turn=?,last_event_key=?,degraded_state='',state_revision=state_revision+1,updated_at=? "
        "WHERE chat_id=? AND session_id=? AND state_revision=? AND inflight_token=?",
        (
            direction,
            proposal_json,
            through_rowid,
            rewrite_revision,
            settings_revision,
            scene_id,
            story_turn,
            until_turn,
            now,
            now,
            story_turn,
            event_key,
            now,
            chat_id,
            session_id,
            expected_director_revision,
            token,
        ),
    )
    return cursor.rowcount == 1


def mark_director_degraded(
    db: sqlite3.Connection,
    chat_id: str,
    session_id: str,
    *,
    token: str,
    expected_director_revision: int,
    category: str,
    story_turn: int,
    now: float,
) -> bool:
    require_active_transaction(db)
    cursor = db.execute(
        "UPDATE director_state SET degraded_state=?,last_attempt_at=?,last_attempt_turn=? "
        "WHERE chat_id=? AND session_id=? AND inflight_token=? AND state_revision=?",
        (category, now, story_turn, chat_id, session_id, token, expected_director_revision),
    )
    return cursor.rowcount == 1


def write_manual_objective(
    db: sqlite3.Connection,
    chat_id: str,
    session_id: str,
    goal: str,
    *,
    expected_director_revision: int,
    now: float,
) -> bool:
    require_active_transaction(db)
    if expected_director_revision == 0:
        db.execute(
            "INSERT INTO director_state(chat_id,session_id) VALUES(?,?) ON CONFLICT(chat_id,session_id) DO NOTHING",
            (chat_id, session_id),
        )
    cursor = db.execute(
        "UPDATE director_state SET goal=?,state_revision=state_revision+1,updated_at=?,inflight_token='',"
        "inflight_started_at=0 WHERE chat_id=? AND session_id=? AND state_revision=?",
        (goal, now, chat_id, session_id, expected_director_revision),
    )
    return cursor.rowcount == 1


def write_manual_direction(
    db: sqlite3.Connection,
    chat_id: str,
    session_id: str,
    direction: str,
    *,
    expected_director_revision: int,
    proposal_json: str,
    scene_id: str,
    through_rowid: int,
    rewrite_revision: int,
    settings_revision: int,
    now: float,
) -> bool:
    require_active_transaction(db)
    if expected_director_revision == 0:
        db.execute(
            "INSERT INTO director_state(chat_id,session_id) VALUES(?,?) ON CONFLICT(chat_id,session_id) DO NOTHING",
            (chat_id, session_id),
        )
    cursor = db.execute(
        "UPDATE director_state SET active_direction=?,active_proposal_json=?,direction_source='user',"
        "direction_scope='next_scene',accepted_scene_id=?,accepted_through_rowid=?,accepted_rewrite_revision=?,"
        "accepted_settings_revision=?,state_revision=state_revision+1,updated_at=?,inflight_token='',"
        "inflight_started_at=0,degraded_state='' WHERE chat_id=? AND session_id=? AND state_revision=?",
        (
            direction,
            proposal_json,
            scene_id,
            through_rowid,
            rewrite_revision,
            settings_revision,
            now,
            chat_id,
            session_id,
            expected_director_revision,
        ),
    )
    return cursor.rowcount == 1


def observe_director_event_key(db: sqlite3.Connection, chat_id: str, session_id: str, event_key: str) -> None:
    require_active_transaction(db)
    db.execute(
        "INSERT INTO director_state(chat_id,session_id,last_event_key) VALUES(?,?,?) "
        "ON CONFLICT(chat_id,session_id) DO UPDATE SET last_event_key=excluded.last_event_key "
        "WHERE director_state.last_event_key=''",
        (chat_id, session_id, event_key),
    )
