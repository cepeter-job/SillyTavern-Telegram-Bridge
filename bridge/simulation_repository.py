"""SQL persistence for canonical simulation tracker state."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any

from bridge.repository_contracts import require_active_transaction

MAX_DOMAIN_RECORDS = 64
MAX_RECORD_BYTES = 16384


def source_identity(db: sqlite3.Connection, chat_id: str, session_id: str, source_rowid: int) -> tuple[str, str]:
    row = db.execute(
        "SELECT role,content FROM messages WHERE chat_id=? AND session_id=? AND id=?",
        (chat_id, session_id, int(source_rowid)),
    ).fetchone()
    if row is None:
        raise ValueError("Simulation source must exist in this session")
    return str(row[0]), hashlib.sha256((str(row[0]) + "\0" + str(row[1])).encode()).hexdigest()


def revision(db: sqlite3.Connection, chat_id: str, session_id: str) -> int:
    row = db.execute(
        "SELECT revision FROM simulation_revisions WHERE chat_id=? AND session_id=?", (chat_id, session_id)
    ).fetchone()
    return int(row[0]) if row else 0


def bump_revision(db: sqlite3.Connection, chat_id: str, session_id: str) -> None:
    require_active_transaction(db)
    db.execute(
        "INSERT INTO simulation_revisions(chat_id,session_id,revision) VALUES(?,?,1) "
        "ON CONFLICT(chat_id,session_id) DO UPDATE SET revision=revision+1",
        (chat_id, session_id),
    )


def validate_publication(
    db: sqlite3.Connection, chat_id: str, session_id: str, source_rowid: int, *, invalidated_from: int | None
) -> tuple[bool, str]:
    """Fence ordered complete-row acceptance, including empty rows and source rewrites."""
    require_active_transaction(db)
    role, digest = source_identity(db, chat_id, session_id, source_rowid)
    latest = db.execute(
        "SELECT COALESCE(MAX(source_rowid),0) FROM simulation_sources WHERE chat_id=? AND session_id=?",
        (chat_id, session_id),
    ).fetchone()[0]
    if invalidated_from is not None and invalidated_from <= latest:
        raise ValueError("Simulation invalidated source suffix requires rollback")
    receipt = db.execute(
        "SELECT source_digest FROM simulation_sources WHERE chat_id=? AND session_id=? AND source_rowid=?",
        (chat_id, session_id, source_rowid),
    ).fetchone()
    if receipt is not None:
        if receipt[0] != digest:
            raise ValueError("Simulation source was rewritten; rollback is required")
        return False, role
    if source_rowid < latest:
        raise ValueError("Simulation publication is older than the accepted source")
    db.execute("INSERT INTO simulation_sources VALUES(?,?,?,?)", (chat_id, session_id, source_rowid, digest))
    return True, role


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
            "WHERE chat_id=? AND session_id=? ORDER BY kind,entity_key LIMIT ?",
            (chat_id, session_id, MAX_DOMAIN_RECORDS * 6),
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT kind,entity_key,value_json,updated_rowid,updated_at FROM simulation_state "
            "WHERE chat_id=? AND session_id=? AND kind=? ORDER BY entity_key LIMIT ?",
            (chat_id, session_id, kind, MAX_DOMAIN_RECORDS),
        ).fetchall()
    return [(str(row[0]), str(row[1]), _decode(row[2]) or {}, int(row[3]), float(row[4])) for row in rows]


def store_state(
    db: sqlite3.Connection,
    chat_id: str,
    session_id: str,
    kind: str,
    entity_key: str,
    value: dict[str, Any] | None,
    *,
    source_rowid: int,
    now: float,
) -> bool:
    require_active_transaction(db)
    existing = load_state(db, chat_id, session_id, kind, entity_key)
    before = existing[0] if existing else None
    before_rowid = existing[1] if existing else 0
    if existing and source_rowid < before_rowid:
        raise ValueError("Simulation state cannot accept an older source")
    if before == value:
        return False
    if (
        value is not None
        and existing is None
        and db.execute(
            "SELECT COUNT(*) FROM simulation_state WHERE chat_id=? AND session_id=? AND kind=?",
            (chat_id, session_id, kind),
        ).fetchone()[0]
        >= MAX_DOMAIN_RECORDS
    ):
        raise ValueError("Simulation domain record limit reached")
    encoded = (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) if value is not None else None
    )
    if encoded is not None and len(encoded.encode()) > MAX_RECORD_BYTES:
        raise ValueError("Simulation state record exceeds storage bound")
    if encoded is None:
        db.execute(
            "DELETE FROM simulation_state WHERE chat_id=? AND session_id=? AND kind=? AND entity_key=?",
            (chat_id, session_id, kind, entity_key),
        )
    else:
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
            json.dumps(before, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            if before is not None
            else None,
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
        "WITH live AS (SELECT kind,entity_key,after_json,after_rowid,"
        "ROW_NUMBER() OVER(PARTITION BY kind ORDER BY entity_key) AS domain_rank "
        "FROM simulation_state_history WHERE change_id IN "
        "(SELECT MAX(change_id) FROM simulation_state_history WHERE chat_id=? AND session_id=? AND source_rowid<=? "
        "GROUP BY kind,entity_key) AND after_json IS NOT NULL) "
        "SELECT kind,entity_key,after_json,after_rowid FROM live WHERE domain_rank<=? "
        "ORDER BY kind,entity_key LIMIT ?",
        (chat_id, session_id, int(through_rowid), MAX_DOMAIN_RECORDS, MAX_DOMAIN_RECORDS * 6),
    ).fetchall()
    state: dict[tuple[str, str], tuple[dict[str, Any], int]] = {}
    for kind, key, after_json, after_rowid in rows:
        decoded = _decode(after_json)
        if decoded is None:
            state.pop((str(kind), str(key)), None)
        else:
            state[(str(kind), str(key))] = (decoded, int(after_rowid))
    return [(kind, key, value, rowid) for (kind, key), (value, rowid) in sorted(state.items())]


def snapshot_history(db: sqlite3.Connection, chat_id: str, session_id: str, through_rowid: int) -> list[dict[str, Any]]:
    # Bound encoded values in SQL before fetching them; snapshots never truncate history.
    count, size = db.execute(
        "SELECT COUNT(*),COALESCE(SUM(COALESCE(LENGTH(CAST(after_json AS BLOB)),4)"
        "+LENGTH(CAST(entity_key AS BLOB))+128),0) FROM simulation_state_history "
        "WHERE chat_id=? AND session_id=? AND source_rowid<=?",
        (chat_id, session_id, through_rowid),
    ).fetchone()
    if count > 4096 or size > 1048576:
        raise ValueError("Simulation history exceeds the bounded checkpoint")
    rows = db.execute(
        "SELECT kind,entity_key,after_json,source_rowid,created_at FROM simulation_state_history "
        "WHERE chat_id=? AND session_id=? AND source_rowid<=? ORDER BY change_id LIMIT 4096",
        (chat_id, session_id, through_rowid),
    ).fetchall()
    return [
        {"kind": kind, "entity_key": key, "value": _decode(value), "source_rowid": source, "created_at": created}
        for kind, key, value, source, created in rows
    ]


def rollback_from_row(
    db: sqlite3.Connection,
    chat_id: str,
    session_id: str,
    rowid: int,
    *,
    now: float,
) -> int:
    require_active_transaction(db)
    count = db.execute(
        "SELECT COUNT(*) FROM simulation_state_history WHERE chat_id=? AND session_id=? AND source_rowid>=?",
        (chat_id, session_id, int(rowid)),
    ).fetchone()[0]
    db.execute(
        "DELETE FROM simulation_state_history WHERE chat_id=? AND session_id=? AND source_rowid>=?",
        (chat_id, session_id, int(rowid)),
    )
    db.execute("DELETE FROM simulation_state WHERE chat_id=? AND session_id=?", (chat_id, session_id))
    for kind, key, value, source in load_states_as_of(db, chat_id, session_id, through_rowid=rowid - 1):
        db.execute(
            "INSERT INTO simulation_state VALUES(?,?,?,?,?,?,?)",
            (
                chat_id,
                session_id,
                kind,
                key,
                json.dumps(value, ensure_ascii=False),
                source,
                now,
            ),
        )
    db.execute(
        "DELETE FROM simulation_sources WHERE chat_id=? AND session_id=? AND source_rowid>=?",
        (chat_id, session_id, int(rowid)),
    )
    db.execute(
        "DELETE FROM simulation_checks WHERE chat_id=? AND session_id=? AND source_rowid>=?",
        (chat_id, session_id, int(rowid)),
    )
    bump_revision(db, chat_id, session_id)
    db.execute(
        "UPDATE simulation_revisions SET backfill_through=MIN(backfill_through,?) WHERE chat_id=? AND session_id=?",
        (max(0, int(rowid) - 1), chat_id, session_id),
    )
    return int(count)


def purge_session(db: sqlite3.Connection, chat_id: str, session_id: str) -> None:
    require_active_transaction(db)
    db.execute(
        "DELETE FROM simulation_state_history WHERE chat_id=? AND session_id=?",
        (chat_id, session_id),
    )
    db.execute("DELETE FROM simulation_state WHERE chat_id=? AND session_id=?", (chat_id, session_id))
    db.execute("DELETE FROM simulation_checks WHERE chat_id=? AND session_id=?", (chat_id, session_id))
    db.execute("DELETE FROM simulation_sources WHERE chat_id=? AND session_id=?", (chat_id, session_id))
    bump_revision(db, chat_id, session_id)
    db.execute(
        "UPDATE simulation_revisions SET backfill_through=0 WHERE chat_id=? AND session_id=?", (chat_id, session_id)
    )


def load_check(
    db: sqlite3.Connection,
    chat_id: str,
    session_id: str,
    request_key: str,
) -> dict[str, Any] | None:
    row = db.execute(
        "SELECT source_rowid,domain,actor,action,dc,roll,modifier,delta,outcome,created_at,source_digest "
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
        "source_digest": str(row[10]),
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
        "chat_id,session_id,request_key,source_rowid,domain,actor,action,dc,roll,modifier,delta,outcome,created_at,source_digest"
        ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
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
            check["source_digest"],
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
    rows = db.execute(
        "SELECT request_key FROM simulation_checks WHERE chat_id=? AND session_id=? "
        "AND (? IS NULL OR source_rowid<=?) ORDER BY check_id DESC LIMIT ?",
        (chat_id, session_id, through_rowid, through_rowid, max(1, min(8, int(limit)))),
    ).fetchall()
    return [item for (key,) in rows if (item := load_check(db, chat_id, session_id, str(key))) is not None]


def bootstrap_through(db: sqlite3.Connection, chat_id: str, session_id: str) -> int:
    row = db.execute(
        "SELECT backfill_through FROM simulation_revisions WHERE chat_id=? AND session_id=?", (chat_id, session_id)
    ).fetchone()
    return int(row[0]) if row else 0


def tracker_progress(db: sqlite3.Connection, chat_id: str, session_id: str, cutoff: int) -> dict[str, Any]:
    latest = db.execute(
        "SELECT COALESCE(MAX(id),0) FROM messages WHERE chat_id=? AND session_id=?", (chat_id, session_id)
    ).fetchone()[0]
    accepted = db.execute(
        "SELECT s.source_rowid,m.created_at FROM simulation_sources s JOIN messages m "
        "ON m.chat_id=s.chat_id AND m.session_id=s.session_id AND m.id=s.source_rowid "
        "WHERE s.chat_id=? AND s.session_id=? AND s.source_rowid<=? ORDER BY s.source_rowid DESC LIMIT 1",
        (chat_id, session_id, cutoff),
    ).fetchone()
    source = int(accepted[0]) if accepted else 0
    return {
        "last_source_rowid": source,
        "last_updated_at": float(accepted[1]) if accepted else None,
        "pending": int(latest) > source,
    }
