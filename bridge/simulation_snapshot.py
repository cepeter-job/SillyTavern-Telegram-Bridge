"""Bounded checkpoint snapshots with portable, explicitly remappable source metadata."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from bridge.repository_contracts import require_active_transaction
from bridge.simulation_context import context_cutoff
from bridge.simulation_repository import (
    bump_revision,
    insert_check,
    load_check,
    load_states_as_of,
    purge_session,
    snapshot_history,
    source_identity,
    store_state,
)
from bridge.sqlite_store import write_transaction


def snapshot_simulation_state(
    db: sqlite3.Connection, chat_id: str, session_id: str, through_rowid: int
) -> dict[str, Any]:
    with write_transaction(db):
        cutoff = context_cutoff(db, chat_id, session_id, through_rowid)
        history = snapshot_history(db, chat_id, session_id, cutoff)
        records = [
            {"kind": kind, "entity_key": key, "value": value, "source_rowid": source}
            for kind, key, value, source in load_states_as_of(db, chat_id, session_id, through_rowid=cutoff)
        ]
        keys = db.execute(
            "SELECT request_key FROM simulation_checks WHERE chat_id=? AND session_id=? "
            "AND source_rowid<=? ORDER BY check_id LIMIT 4097",
            (chat_id, session_id, cutoff),
        ).fetchall()
        receipts = db.execute(
            "SELECT source_rowid,source_digest FROM simulation_sources WHERE chat_id=? "
            "AND session_id=? AND source_rowid<=? ORDER BY source_rowid LIMIT 20001",
            (chat_id, session_id, cutoff),
        ).fetchall()
        if len(keys) > 4096 or len(receipts) > 20000:
            raise ValueError("Simulation history exceeds the bounded checkpoint")
        result = {
            "format": 1,
            "history": history,
            "records": records,
            "checks": [load_check(db, chat_id, session_id, name) for (name,) in keys],
            "sources": [{"source_rowid": source, "source_digest": digest} for source, digest in receipts],
        }
        if len(json.dumps(result, ensure_ascii=False).encode()) > 1048576:
            raise ValueError("Simulation state exceeds the bounded checkpoint")
        return result


def restore_simulation_snapshot(db: sqlite3.Connection, chat_id: str, session_id: str, payload: dict[str, Any]) -> None:
    require_active_transaction(db)
    if payload and (payload.get("format") != 1 or not isinstance(payload.get("history"), list)):
        raise ValueError("Simulation checkpoint lacks reversible history")
    if len(payload.get("history", [])) > 4096 or len(json.dumps(payload, ensure_ascii=False).encode()) > 1048576:
        raise ValueError("Simulation history exceeds the bounded checkpoint")
    purge_session(db, chat_id, session_id)
    for item in payload.get("history", []):
        source_identity(db, chat_id, session_id, item["source_rowid"])
        store_state(
            db,
            chat_id,
            session_id,
            item["kind"],
            item["entity_key"],
            item["value"],
            source_rowid=item["source_rowid"],
            now=item["created_at"],
        )
    records = [
        {"kind": kind, "entity_key": key, "value": value, "source_rowid": source}
        for kind, key, value, source in load_states_as_of(db, chat_id, session_id, through_rowid=2**63 - 1)
    ]
    if records != payload.get("records", []):
        raise ValueError("Simulation checkpoint history differs from its current records")
    for check in payload.get("checks", []):
        _, digest = source_identity(db, chat_id, session_id, check["source_rowid"])
        restored = check | {"request_key": "restored:" + check["request_key"], "source_digest": digest}
        insert_check(db, chat_id, session_id, restored)
    for item in payload.get("sources", []):
        _, digest = source_identity(db, chat_id, session_id, item["source_rowid"])
        if digest != item["source_digest"]:
            raise ValueError("Simulation checkpoint source differs from the restored transcript")
        db.execute(
            "INSERT INTO simulation_sources VALUES(?,?,?,?)", (chat_id, session_id, item["source_rowid"], digest)
        )
    bump_revision(db, chat_id, session_id)
