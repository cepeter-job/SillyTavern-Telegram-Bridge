"""SQL persistence for canonical simulation tracker state."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from bridge.repository_contracts import require_active_transaction


def _decode(value: str | None) -> dict[str, Any] | None:
    if value is None:
        return None
    raw = json.loads(value)
    return raw if isinstance(raw, dict) else None


def load_state(
    db: sqlite3.Connection,
    chat_id: str,
    session_id: str,
    kind: str,
    entity_key: str,
) -> tuple[dict[str, Any], int, float] | None:
    row = db.execute(
        "SELECT value_json,updated_rowid,updated_at FROM simulation_state "
        "WHERE chat_id=? AND session_id=? AND kind=? AND entity_key=?",
        (chat_id, session_id, kind, entity_key),
    ).fetchone()
    if row is None:
        return None
    return _decode(row[0]) or {}, int(row[1]), float(row[2])


def list_states(
    db: sqlite3.Connection,
    chat_id: str,
    session_id: str,
    *,
    kind: str | None = None,
) -> list[tuple[str, str, dict[str, Any], int, float]]:
    if kind is None:
        rows = db.execute(
            "SELECT kind,entity_key,value_json,updated_rowid,updated_at FROM simulation_state "
            "WHERE chat_id=? AND session_id=? ORDER BY kind,entity_key",
            (chat_id, session_id),
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT kind,entity_key,value_json,updated_rowid,updated_at FROM simulation_state "
            "WHERE chat_id=? AND session_id=? AND kind=? ORDER BY entity_key",
            (chat_id, session_id, kind),
        ).fetchall()
    return [
        (str(row[0]), str(row[1]), _decode(row[2]) or {}, int(row[3]), float(row[4]))
        for row in rows
    ]


def store_state(
    db: sqlite3.Connection,
    chat_id: str,
    session_id: str,
    kind: str,
    entity_key: str,
    value: dict[str, Any],
    *,
    source_rowid: int,
    now: float,
) -> bool:
    require_active_transaction(db)
    existing = load_state(db, chat_id, session_id, kind, entity_key)
    before = existing[0] if existing else None
    before_rowid = existing[1] if existing else 0
    if before == value:
        return False
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if len(encoded) > 16384:
        raise ValueError("Simulation state record exceeds storage bound")
    db.execute(
        "INSERT INTO simulation_state(chat_id,session_id,kind,entity_key,value_json,updated_rowid,updated_at) "
        "VALUES(?,?,?,?,?,?,?) ON CONFLICT(chat_id,session_id,kind,entity_key) DO UPDATE SET "
        "value_json=excluded.value_json,updated_rowid=excluded.updated_rowid,updated_at=excluded.updated_at",
        (chat_id, session_id, kind, entity_key, encoded, int(source_rowid), float(now)),
    )
    db.execute(
        "INSERT INTO simulation_state_history("
        "chat_id,session_id,kind,entity_key,before_json,after_json,before_rowid,after_rowid,source_rowid,created_at"
        ") VALUES(?,?,?,?,?,?,?,?,?,?)",
        (
            chat_id,
            session_id,
            kind,
            entity_key,
            json.dumps(before, ensure_ascii=False, sort_keys=True, separators=(",", ":")) if before is not None else None,
            encoded,
            int(before_rowid),
            int(source_rowid),
            int(source_rowid),
            float(now),
        ),
    )
    return True


def load_states_as_of(
    db: sqlite3.Connection,
    chat_id: str,
    session_id: str,
    *,
    through_rowid: int,
) -> list[tuple[str, str, dict[str, Any], int]]:
    rows = db.execute(
        "SELECT kind,entity_key,after_json,after_rowid FROM simulation_state_history "
        "WHERE chat_id=? AND session_id=? AND source_rowid<=? "
        "ORDER BY source_rowid,change_id",
        (chat_id, session_id, int(through_rowid)),
    ).fetchall()
    state: dict[tuple[str, str], tuple[dict[str, Any], int]] = {}
    for kind, key, after_json, after_rowid in rows:
        decoded = _decode(after_json)
        if decoded is None:
            state.pop((str(kind), str(key)), None)
        else:
            state[(str(kind), str(key))] = (decoded, int(after_rowid))
    return [(kind, key, value, rowid) for (kind, key), (value, rowid) in sorted(state.items())]


def rollback_from_row(
    db: sqlite3.Connection,
    chat_id: str,
    session_id: str,
    rowid: int,
    *,
    now: float,
) -> int:
    require_active_transaction(db)
    rows = db.execute(
        "SELECT change_id,kind,entity_key,before_json,before_rowid FROM simulation_state_history "
        "WHERE chat_id=? AND session_id=? AND source_rowid>=? ORDER BY source_rowid DESC,change_id DESC",
        (chat_id, session_id, int(rowid)),
    ).fetchall()
    for change_id, kind, key, before_json, before_rowid in rows:
        before = _decode(before_json)
        if before is None:
            db.execute(
                "DELETE FROM simulation_state WHERE chat_id=? AND session_id=? AND kind=? AND entity_key=?",
                (chat_id, session_id, kind, key),
            )
        else:
            encoded = json.dumps(before, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            db.execute(
                "INSERT INTO simulation_state(chat_id,session_id,kind,entity_key,value_json,updated_rowid,updated_at) "
                "VALUES(?,?,?,?,?,?,?) ON CONFLICT(chat_id,session_id,kind,entity_key) DO UPDATE SET "
                "value_json=excluded.value_json,updated_rowid=excluded.updated_rowid,updated_at=excluded.updated_at",
                (chat_id, session_id, kind, key, encoded, int(before_rowid), float(now)),
            )
        db.execute("DELETE FROM simulation_state_history WHERE change_id=?", (int(change_id),))
    db.execute(
        "DELETE FROM simulation_checks WHERE chat_id=? AND session_id=? AND source_rowid>=?",
        (chat_id, session_id, int(rowid)),
    )
    return len(rows)


def purge_session(db: sqlite3.Connection, chat_id: str, session_id: str) -> None:
    require_active_transaction(db)
    db.execute(
        "DELETE FROM simulation_state_history WHERE chat_id=? AND session_id=?",
        (chat_id, session_id),
    )
    db.execute("DELETE FROM simulation_state WHERE chat_id=? AND session_id=?", (chat_id, session_id))
    db.execute("DELETE FROM simulation_checks WHERE chat_id=? AND session_id=?", (chat_id, session_id))


def load_check(
    db: sqlite3.Connection,
    chat_id: str,
    session_id: str,
    request_key: str,
) -> dict[str, Any] | None:
    row = db.execute(
        "SELECT source_rowid,domain,actor,action,dc,roll,modifier,delta,outcome,created_at "
        "FROM simulation_checks WHERE chat_id=? AND session_id=? AND request_key=?",
        (chat_id, session_id, request_key),
    ).fetchone()
    if row is None:
        return None
    return {
        "request_key": request_key,
        "source_rowid": int(row[0]),
        "domain": str(row[1]),
        "actor": str(row[2]),
        "action": str(row[3]),
        "dc": int(row[4]),
        "roll": int(row[5]),
        "modifier": int(row[6]),
        "delta": int(row[7]),
        "outcome": str(row[8]),
        "created_at": float(row[9]),
    }


def insert_check(
    db: sqlite3.Connection,
    chat_id: str,
    session_id: str,
    check: dict[str, Any],
) -> dict[str, Any]:
    require_active_transaction(db)
    db.execute(
        "INSERT INTO simulation_checks("
        "chat_id,session_id,request_key,source_rowid,domain,actor,action,dc,roll,modifier,delta,outcome,created_at"
        ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            chat_id,
            session_id,
            check["request_key"],
            int(check["source_rowid"]),
            check["domain"],
            check["actor"],
            check["action"],
            int(check["dc"]),
            int(check["roll"]),
            int(check["modifier"]),
            int(check["delta"]),
            check["outcome"],
            float(check["created_at"]),
        ),
    )
    return check


def list_checks(
    db: sqlite3.Connection,
    chat_id: str,
    session_id: str,
    *,
    through_rowid: int | None = None,
    limit: int = 3,
) -> list[dict[str, Any]]:
    sql = (
        "SELECT request_key FROM simulation_checks WHERE chat_id=? AND session_id=?"
        + (" AND source_rowid<=?" if through_rowid is not None else "")
        + " ORDER BY check_id DESC LIMIT ?"
    )
    params: tuple[Any, ...] = (
        (chat_id, session_id, int(through_rowid), max(1, min(8, int(limit))))
        if through_rowid is not None
        else (chat_id, session_id, max(1, min(8, int(limit))))
    )
    rows = db.execute(sql, params).fetchall()
    return [
        item
        for key, in rows
        if (item := load_check(db, chat_id, session_id, str(key))) is not None
    ]
