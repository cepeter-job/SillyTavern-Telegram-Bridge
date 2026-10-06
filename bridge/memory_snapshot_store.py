"""Canonical knowledge capture/restoration above the pure SQL snapshot repository."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from bridge.memory_artifact_store import load_artifact_classification, store_artifact_visibility
from bridge.memory_fact_store import digest_value, load_source, restore_snapshot_fact
from bridge.memory_scope_store import eligible_fact, resolve_memory_scope
from bridge.memory_snapshot_repository import restore_local_memory_snapshot as _restore_rows
from bridge.memory_snapshot_repository import snapshot_local_memory as _snapshot_rows


def snapshot_local_memory(db: sqlite3.Connection, chat_id: str, session_id: str, through_rowid: int) -> dict[str, Any]:
    scope = resolve_memory_scope(
        db,
        chat_id,
        {"session_id": session_id},
        {},
        through_rowid=through_rowid,
        consumer="narrator",
    )
    if scope is None:
        raise ValueError("Missing checkpoint story incarnation")
    snapshot = _snapshot_rows(db, chat_id, session_id, through_rowid)
    episodes = []
    for item in snapshot["episodic"]:
        stored = eligible_fact(db, scope, int(item["memory_id"]))
        if stored is None:
            continue
        if stored.provenance_kind == "transcript_part":
            source = load_source(db, stored.evidence.source_document_id)
            if source is None:
                continue
            item["provenance"] = {
                "kind": "transcript_part",
                "source_start_rowid": source.start_id,
                "source_end_rowid": source.end_id,
                "start_offset": source.start_offset,
                "end_offset": source.end_offset,
                "source_digest": source.source_digest,
            }
        else:
            item["provenance"] = {"kind": "explicit", "accepted_after_rowid": stored.accepted_after_rowid}
        episodes.append(item)
    snapshot["episodic"] = episodes
    classifications = {}
    for kind in ("summary", "scene"):
        classification = load_artifact_classification(db, chat_id, session_id, kind)
        if classification and classification[1] <= scope.through_rowid:
            classifications[kind] = {
                "payload_digest": classification[0],
                "through_rowid": classification[1],
                "blocks": classification[2],
            }
    if "summary" not in classifications:
        snapshot["summary"] = None
    snapshot["classifications"] = classifications
    if len(json.dumps(snapshot, ensure_ascii=False).encode("utf-8")) > 1048576:
        raise ValueError("Local memory proof is too large for a bounded pre-finale snapshot")
    return snapshot


def restore_local_memory_snapshot(
    db: sqlite3.Connection, chat_id: str, session_id: str, memory: dict[str, Any]
) -> None:
    """Fresh target IDs and pending outbox rows; origin external ACKs are never copied."""
    if not db.in_transaction:
        raise RuntimeError("Memory restoration requires its caller-owned transaction")
    _restore_rows(db, chat_id, session_id, dict(memory, episodic=[]))
    for item in memory.get("episodic", []):
        restore_snapshot_fact(db, chat_id, session_id, item)
    for kind, classification in memory.get("classifications", {}).items():
        if kind == "summary":
            row = db.execute(
                "SELECT summary,covered_until_rowid FROM session_summaries WHERE chat_id=? AND session_id=?",
                (chat_id, session_id),
            ).fetchone()
        elif kind == "scene":
            row = db.execute(
                "SELECT state_json,updated_through_rowid FROM scene_states WHERE chat_id=? AND session_id=?",
                (chat_id, session_id),
            ).fetchone()
        else:
            continue
        if (
            row
            and row[1] == classification["through_rowid"]
            and digest_value(row[0]) == classification["payload_digest"]
        ):
            store_artifact_visibility(db, chat_id, session_id, kind, classification["blocks"])
