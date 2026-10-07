"""SQL-only reservation primitives for unfinished, session-scoped action checks."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any

from bridge.repository_contracts import require_active_transaction


def read_action_scope(db: sqlite3.Connection, chat_id: str, session_id: str) -> tuple[float, int, str] | None:
    row = db.execute(
        "SELECT s.created_at,s.character_file,s.persona_id,s.world_file,s.system_prompt,s.author_note,"
        "COALESCE(n.history_revision,0),COALESCE(n.rewrite_revision,0),COALESCE(p.settings_revision,0),"
        "COALESCE((SELECT value FROM meta WHERE key='conversation_epoch:'||s.chat_id||':'||s.session_id),'0'),"
        "(SELECT COALESCE(MAX(id),0) FROM messages WHERE chat_id=s.chat_id AND session_id=s.session_id) "
        "FROM sessions s LEFT JOIN narrative_state n USING(chat_id,session_id) "
        "LEFT JOIN narrative_settings p USING(chat_id,session_id) WHERE s.chat_id=? AND s.session_id=?",
        (chat_id, session_id),
    ).fetchone()
    if row is None:
        return None
    digest = hashlib.sha256(json.dumps(tuple(row), ensure_ascii=False).encode()).hexdigest()
    return float(row[0]), int(row[-1]), digest


def load_preflight(db: sqlite3.Connection, chat_id: str, session_id: str, request_key: str) -> tuple | None:
    return db.execute(
        "SELECT scope_digest,lease_token,lease_until,receipt_json,input_digest FROM action_preflights "
        "WHERE chat_id=? AND session_id=? AND request_key=?",
        (chat_id, session_id, request_key),
    ).fetchone()


def reserve_preflight(
    db: sqlite3.Connection,
    chat_id: str,
    session_id: str,
    request_key: str,
    scope_digest: str,
    input_digest: str,
    token: str,
    now: float,
) -> None:
    require_active_transaction(db)
    row = load_preflight(db, chat_id, session_id, request_key)
    if row is not None:
        if row[0] != scope_digest:
            raise ValueError("The story changed since this action was admitted; submit a new action.")
        if row[3] or row[2] > now:
            raise ValueError("This action is already being evaluated; retry the same turn shortly.")
        db.execute(
            "UPDATE action_preflights SET lease_token=?,lease_until=? "
            "WHERE chat_id=? AND session_id=? AND request_key=?",
            (token, now + 120, chat_id, session_id, request_key),
        )
        return
    if (
        db.execute(
            "SELECT COUNT(*) FROM action_preflights WHERE chat_id=? AND session_id=?", (chat_id, session_id)
        ).fetchone()[0]
        >= 64
    ):
        raise ValueError("Too many unfinished action checks; recover an existing turn or reset the session.")
    db.execute(
        "INSERT INTO action_preflights VALUES(?,?,?,?,?,?,?,?)",
        (chat_id, session_id, request_key, scope_digest, input_digest, token, now + 120, ""),
    )


def preflight_owned(
    db: sqlite3.Connection,
    chat_id: str,
    session_id: str,
    request_key: str,
    token: str,
    now: float,
) -> bool:
    row = load_preflight(db, chat_id, session_id, request_key)
    return bool(row is not None and row[1] == token and row[2] > now and not row[3])


def finish_preflight(
    db: sqlite3.Connection,
    chat_id: str,
    session_id: str,
    request_key: str,
    token: str,
    receipt: dict[str, Any],
) -> None:
    require_active_transaction(db)
    encoded = json.dumps(receipt, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if len(encoded.encode()) > 12000:
        raise ValueError("Action receipt exceeds its storage bound")
    cursor = db.execute(
        "UPDATE action_preflights SET receipt_json=?,lease_token='',lease_until=0 "
        "WHERE chat_id=? AND session_id=? AND request_key=? AND lease_token=? AND receipt_json=''",
        (encoded, chat_id, session_id, request_key, token),
    )
    if cursor.rowcount != 1:
        raise ValueError("Action lease changed before acceptance")


def abandon_preflight(
    db: sqlite3.Connection,
    chat_id: str,
    session_id: str,
    request_key: str,
    token: str,
) -> None:
    require_active_transaction(db)
    db.execute(
        "DELETE FROM action_preflights WHERE chat_id=? AND session_id=? AND request_key=? "
        "AND lease_token=? AND receipt_json=''",
        (chat_id, session_id, request_key, token),
    )


def delete_preflight(db: sqlite3.Connection, chat_id: str, session_id: str, request_key: str) -> None:
    require_active_transaction(db)
    db.execute(
        "DELETE FROM action_preflights WHERE chat_id=? AND session_id=? AND request_key=?",
        (chat_id, session_id, request_key),
    )
