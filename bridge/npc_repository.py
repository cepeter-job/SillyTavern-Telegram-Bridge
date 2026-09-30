"""SQL-only persistence for the session-scoped NPC Bank."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable

from bridge.npc_types import NpcEntity
from bridge.repository_contracts import require_active_transaction


def _normalize_name(value: str) -> str:
    return " ".join(str(value or "").split()).casefold()


def _decode_aliases(value: str) -> tuple[str, ...]:
    try:
        raw = json.loads(str(value or "[]"))
    except (TypeError, json.JSONDecodeError):
        return ()
    if not isinstance(raw, list):
        return ()
    return tuple(str(item) for item in raw if str(item or "").strip())


def _entity_from_row(row) -> NpcEntity:
    return NpcEntity(
        npc_id=int(row[0]),
        chat_id=str(row[1]),
        session_id=str(row[2]),
        canonical_name=str(row[3]),
        display_name=str(row[4]),
        aliases=_decode_aliases(row[5]),
        first_seen_rowid=int(row[6]),
        last_seen_rowid=int(row[7]),
        created_at=float(row[8]),
        updated_at=float(row[9]),
    )


_ENTITY_COLUMNS = (
    "npc_id,chat_id,session_id,canonical_name,display_name,aliases_json,"
    "first_seen_rowid,last_seen_rowid,created_at,updated_at"
)


def list_npc_entities(db: sqlite3.Connection, chat_id: str, session_id: str) -> list[NpcEntity]:
    rows = db.execute(
        f"SELECT {_ENTITY_COLUMNS} FROM npc_entities WHERE chat_id=? AND session_id=? ORDER BY npc_id",  # noqa: S608
        (chat_id, session_id),
    ).fetchall()
    return [_entity_from_row(row) for row in rows]


def find_npc_exact(db: sqlite3.Connection, chat_id: str, session_id: str, canonical_name: str) -> NpcEntity | None:
    wanted = _normalize_name(canonical_name)
    matches = [
        item for item in list_npc_entities(db, chat_id, session_id) if _normalize_name(item.canonical_name) == wanted
    ]
    return matches[0] if len(matches) == 1 else None


def find_npc_by_name_or_alias(
    db: sqlite3.Connection, chat_id: str, session_id: str, normalized_name: str
) -> NpcEntity | None:
    wanted = _normalize_name(normalized_name)
    matches: list[NpcEntity] = []
    for item in list_npc_entities(db, chat_id, session_id):
        names = {_normalize_name(item.canonical_name), *(_normalize_name(alias) for alias in item.aliases)}
        if wanted in names:
            matches.append(item)
    return matches[0] if len(matches) == 1 else None


def insert_npc_entity(
    db: sqlite3.Connection,
    chat_id: str,
    session_id: str,
    canonical_name: str,
    display_name: str,
    aliases: Iterable[str],
    source_rowid: int,
    now: float,
) -> int:
    require_active_transaction(db)
    cursor = db.execute(
        """
        INSERT INTO npc_entities(
            chat_id,session_id,canonical_name,display_name,aliases_json,
            first_seen_rowid,last_seen_rowid,created_at,updated_at
        ) VALUES(?,?,?,?,?,?,?,?,?)
        """,
        (
            str(chat_id),
            str(session_id),
            _normalize_name(canonical_name),
            " ".join(str(display_name or "").split()).strip(),
            json.dumps(tuple(str(alias) for alias in aliases), ensure_ascii=False),
            int(source_rowid),
            int(source_rowid),
            float(now),
            float(now),
        ),
    )
    return int(cursor.lastrowid)


def get_npc_extraction_coverage(db: sqlite3.Connection, chat_id: str, session_id: str) -> int:
    row = db.execute(
        "SELECT updated_through_rowid FROM npc_extraction_state WHERE chat_id=? AND session_id=?",
        (chat_id, session_id),
    ).fetchone()
    return int(row[0]) if row else 0


def set_npc_extraction_coverage(db: sqlite3.Connection, chat_id: str, session_id: str, rowid: int, now: float) -> None:
    require_active_transaction(db)
    db.execute(
        """
        INSERT INTO npc_extraction_state(chat_id,session_id,updated_through_rowid,updated_at)
        VALUES(?,?,?,?)
        ON CONFLICT(chat_id,session_id) DO UPDATE SET
            updated_through_rowid=excluded.updated_through_rowid,
            updated_at=excluded.updated_at
        """,
        (chat_id, session_id, int(rowid), float(now)),
    )
