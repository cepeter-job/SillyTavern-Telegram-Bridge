"""SQL-only persistence for the session-scoped NPC Bank."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from typing import Any

from bridge.repository_contracts import NpcEntity, NpcFieldChange, NpcFieldState, require_active_transaction


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


def _entity_from_row(row: tuple[Any, ...]) -> NpcEntity:
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
    if cursor.lastrowid is None:
        raise RuntimeError("NPC entity insert did not return a row id")
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


def _decode_json(value: str) -> Any:
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return None


def _decode_known_by(value: str) -> tuple[str, ...]:
    raw = _decode_json(str(value or "[]"))
    return tuple(str(item) for item in raw) if isinstance(raw, list) else ()


def load_npc_fields(db: sqlite3.Connection, npc_id: int) -> dict[str, NpcFieldState]:
    rows = db.execute(
        """
        SELECT npc_id,field_key,value_json,field_mode,visibility,known_by_json,updated_rowid,updated_at
        FROM npc_fields WHERE npc_id=? ORDER BY field_key
        """,
        (int(npc_id),),
    ).fetchall()
    return {
        str(row[1]): NpcFieldState(
            npc_id=int(row[0]),
            field_key=str(row[1]),
            value=_decode_json(row[2]),
            field_mode=str(row[3]),
            visibility=str(row[4]),
            known_by=_decode_known_by(row[5]),
            updated_rowid=int(row[6]),
            updated_at=float(row[7]),
        )
        for row in rows
    }


def upsert_npc_field(db: sqlite3.Connection, state: NpcFieldState) -> None:
    require_active_transaction(db)
    db.execute(
        """
        INSERT INTO npc_fields(
            npc_id,field_key,value_json,field_mode,visibility,known_by_json,updated_rowid,updated_at
        ) VALUES(?,?,?,?,?,?,?,?)
        ON CONFLICT(npc_id,field_key) DO UPDATE SET
            value_json=excluded.value_json,
            field_mode=excluded.field_mode,
            visibility=excluded.visibility,
            known_by_json=excluded.known_by_json,
            updated_rowid=excluded.updated_rowid,
            updated_at=excluded.updated_at
        """,
        (
            state.npc_id,
            state.field_key,
            json.dumps(state.value, ensure_ascii=False),
            state.field_mode,
            state.visibility,
            json.dumps(state.known_by, ensure_ascii=False),
            state.updated_rowid,
            state.updated_at,
        ),
    )


def delete_npc_field(db: sqlite3.Connection, npc_id: int, field_key: str) -> None:
    require_active_transaction(db)
    db.execute("DELETE FROM npc_fields WHERE npc_id=? AND field_key=?", (int(npc_id), str(field_key)))


def insert_npc_field_change(
    db: sqlite3.Connection,
    npc_id: int,
    field_key: str,
    operation: str,
    before: NpcFieldState | None,
    after: NpcFieldState | None,
    source_rowid: int,
    now: float,
) -> int:
    require_active_transaction(db)

    def value(state: NpcFieldState | None, attr: str) -> Any:
        return getattr(state, attr) if state is not None else None

    cursor = db.execute(
        """
        INSERT INTO npc_field_history(
            npc_id,field_key,operation,before_json,after_json,before_mode,after_mode,
            before_visibility,after_visibility,before_known_by_json,after_known_by_json,
            source_rowid,created_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            int(npc_id),
            str(field_key),
            str(operation),
            json.dumps(value(before, "value"), ensure_ascii=False) if before is not None else None,
            json.dumps(value(after, "value"), ensure_ascii=False) if after is not None else None,
            value(before, "field_mode"),
            value(after, "field_mode"),
            value(before, "visibility"),
            value(after, "visibility"),
            json.dumps(value(before, "known_by"), ensure_ascii=False) if before is not None else None,
            json.dumps(value(after, "known_by"), ensure_ascii=False) if after is not None else None,
            int(source_rowid),
            float(now),
        ),
    )
    if cursor.lastrowid is None:
        raise RuntimeError("NPC field history insert did not return a row id")
    return int(cursor.lastrowid)


def list_npc_field_history(db: sqlite3.Connection, npc_id: int) -> list[NpcFieldChange]:
    rows = db.execute(
        """
        SELECT change_id,npc_id,field_key,operation,before_json,after_json,before_mode,after_mode,
               before_visibility,after_visibility,before_known_by_json,after_known_by_json,
               source_rowid,created_at
        FROM npc_field_history WHERE npc_id=? ORDER BY source_rowid,change_id
        """,
        (int(npc_id),),
    ).fetchall()
    result = []
    for row in rows:
        source_rowid = int(row[12])
        created_at = float(row[13])

        def snapshot(
            value_json: Any,
            mode: Any,
            visibility: Any,
            known_by_json: Any,
            *,
            row_npc_id: int,
            field_key: str,
            row_source: int,
            row_created_at: float,
        ) -> NpcFieldState | None:
            if value_json is None or mode is None:
                return None
            return NpcFieldState(
                npc_id=row_npc_id,
                field_key=field_key,
                value=_decode_json(value_json),
                field_mode=str(mode),
                visibility=str(visibility or "shared"),
                known_by=_decode_known_by(known_by_json or "[]"),
                updated_rowid=row_source,
                updated_at=row_created_at,
            )

        row_npc_id = int(row[1])
        field_key = str(row[2])
        result.append(
            NpcFieldChange(
                change_id=int(row[0]),
                npc_id=row_npc_id,
                field_key=field_key,
                operation=str(row[3]),
                before=snapshot(
                    row[4],
                    row[6],
                    row[8],
                    row[10],
                    row_npc_id=row_npc_id,
                    field_key=field_key,
                    row_source=source_rowid,
                    row_created_at=created_at,
                ),
                after=snapshot(
                    row[5],
                    row[7],
                    row[9],
                    row[11],
                    row_npc_id=row_npc_id,
                    field_key=field_key,
                    row_source=source_rowid,
                    row_created_at=created_at,
                ),
                source_rowid=source_rowid,
                created_at=created_at,
            )
        )
    return result


def delete_npc_field_change(db: sqlite3.Connection, change_id: int) -> bool:
    require_active_transaction(db)
    cursor = db.execute("DELETE FROM npc_field_history WHERE change_id=?", (int(change_id),))
    return int(cursor.rowcount) > 0


def delete_npc_history_from_row(db: sqlite3.Connection, npc_id: int, rowid: int) -> int:
    require_active_transaction(db)
    cursor = db.execute(
        "DELETE FROM npc_field_history WHERE npc_id=? AND source_rowid>=?",
        (int(npc_id), int(rowid)),
    )
    return max(0, int(cursor.rowcount))


def update_npc_entity_seen(db: sqlite3.Connection, npc_id: int, source_rowid: int, now: float) -> None:
    require_active_transaction(db)
    db.execute(
        "UPDATE npc_entities SET last_seen_rowid=MAX(last_seen_rowid,?),updated_at=? WHERE npc_id=?",
        (int(source_rowid), float(now), int(npc_id)),
    )


def delete_npc_entity(db: sqlite3.Connection, npc_id: int) -> None:
    require_active_transaction(db)
    db.execute("DELETE FROM npc_entities WHERE npc_id=?", (int(npc_id),))


def purge_npc_session_rows(db: sqlite3.Connection, chat_id: str, session_id: str) -> int:
    require_active_transaction(db)
    row = db.execute(
        "SELECT COUNT(*) FROM npc_entities WHERE chat_id=? AND session_id=?",
        (chat_id, session_id),
    ).fetchone()
    count = int(row[0]) if row else 0
    db.execute(
        """
        DELETE FROM npc_field_history
        WHERE npc_id IN (SELECT npc_id FROM npc_entities WHERE chat_id=? AND session_id=?)
        """,
        (chat_id, session_id),
    )
    db.execute(
        """
        DELETE FROM npc_fields
        WHERE npc_id IN (SELECT npc_id FROM npc_entities WHERE chat_id=? AND session_id=?)
        """,
        (chat_id, session_id),
    )
    db.execute("DELETE FROM npc_extraction_state WHERE chat_id=? AND session_id=?", (chat_id, session_id))
    db.execute("DELETE FROM npc_entities WHERE chat_id=? AND session_id=?", (chat_id, session_id))
    return count


def set_npc_entity_last_seen(db: sqlite3.Connection, npc_id: int, source_rowid: int, now: float) -> None:
    require_active_transaction(db)
    db.execute(
        "UPDATE npc_entities SET last_seen_rowid=?,updated_at=? WHERE npc_id=?",
        (int(source_rowid), float(now), int(npc_id)),
    )


def load_npc_fields_as_of(db: sqlite3.Connection, npc_id: int, through_rowid: int) -> dict[str, NpcFieldState]:
    """Reconstruct NPC field state at or before one transcript row."""
    fields: dict[str, NpcFieldState] = {}
    for change in list_npc_field_history(db, npc_id):
        if change.source_rowid > int(through_rowid):
            break
        if change.after is None:
            fields.pop(change.field_key, None)
        else:
            fields[change.field_key] = change.after
    return fields


def snapshot_npc_state(
    db: sqlite3.Connection, chat_id: str, session_id: str, through_rowid: int
) -> list[dict[str, Any]]:
    """Capture checkpoint-time local NPC state without reading future extraction results."""
    cursor = db.execute(
        "SELECT * FROM npc_entities WHERE chat_id=? AND session_id=? AND first_seen_rowid<=? ORDER BY npc_id LIMIT 257",
        (chat_id, session_id, through_rowid),
    )
    try:
        columns = [item[0] for item in cursor.description]
        entities = [dict(zip(columns, row, strict=True)) for row in cursor]
    finally:
        cursor.close()
    if len(entities) > 256:
        raise ValueError("NPC state exceeds the bounded pre-finale snapshot")
    budget = 0
    for entity in entities:
        values = npc_snapshot_fields(db, entity["npc_id"], through_rowid)
        if len(values) > 128:
            raise ValueError("NPC fields exceed the bounded pre-finale snapshot")
        budget += len(json.dumps(values, ensure_ascii=False))
        if budget > 1048576:
            raise ValueError("NPC state is too large for a pre-finale snapshot")
        entity["fields"] = values
        entity["last_seen_rowid"] = min(entity["last_seen_rowid"], through_rowid)
    return entities


def restore_npc_snapshot(db: sqlite3.Connection, chat_id: str, session_id: str, entities: list[dict]) -> None:
    """Create independent NPC IDs and a checkpoint-valid baseline for future rewinds."""
    require_active_transaction(db)
    for entity in entities:
        npc_id = insert_npc_entity(
            db,
            chat_id,
            session_id,
            entity["canonical_name"],
            entity["display_name"],
            json.loads(entity["aliases_json"]),
            entity["first_seen_rowid"],
            entity["created_at"],
        )
        set_npc_entity_last_seen(db, npc_id, entity["last_seen_rowid"], entity["updated_at"])
        for item in entity["fields"]:
            field = NpcFieldState(
                npc_id,
                item["field_key"],
                json.loads(item["value_json"]),
                item["field_mode"],
                item["visibility"],
                tuple(json.loads(item["known_by_json"])),
                item["updated_rowid"],
                item["updated_at"],
            )
            upsert_npc_field(db, field)
            insert_npc_field_change(
                db, npc_id, field.field_key, "set", None, field, field.updated_rowid, field.updated_at
            )


def npc_snapshot_fields(db: sqlite3.Connection, npc_id: int, through_rowid: int) -> list[dict[str, Any]]:
    cursor = db.execute(
        "WITH history AS (SELECT *,ROW_NUMBER() OVER (PARTITION BY field_key "
        "ORDER BY source_rowid DESC,change_id DESC) AS rank FROM npc_field_history "
        "WHERE npc_id=? AND source_rowid<=?) "
        "SELECT npc_id,field_key,substr(after_json,1,1048577) AS value_json,after_mode AS field_mode,"
        "after_visibility AS visibility,after_known_by_json AS known_by_json,source_rowid AS updated_rowid,"
        "created_at AS updated_at FROM history WHERE rank=1 AND after_json IS NOT NULL "
        "UNION ALL SELECT npc_id,field_key,substr(value_json,1,1048577),field_mode,visibility,known_by_json,"
        "updated_rowid,updated_at FROM npc_fields f WHERE npc_id=? AND updated_rowid<=? "
        "AND NOT EXISTS(SELECT 1 FROM npc_field_history h WHERE h.npc_id=f.npc_id AND h.field_key=f.field_key) "
        "ORDER BY field_key LIMIT 129",
        (npc_id, through_rowid, npc_id, through_rowid),
    )
    try:
        columns = [item[0] for item in cursor.description]
        return [dict(zip(columns, row, strict=True)) for row in cursor]
    finally:
        cursor.close()
