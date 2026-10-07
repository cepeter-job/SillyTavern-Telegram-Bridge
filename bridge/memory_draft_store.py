"""Atomic private source-part progress; model calls happen outside write transactions."""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from bridge.memory_fact_store import load_source
from bridge.memory_store import (
    LEASE_SECONDS,
    MemoryClaim,
    MemorySource,
    next_source_segment,
    source_is_valid,
    store_segment,
)
from bridge.simulation_repository import revision as simulation_revision
from bridge.sqlite_store import write_transaction

MAX_DRAFT_BYTES = 262144
MAX_CHECKPOINTS = 8
Payload = dict[str, Any]


@dataclass(frozen=True)
class MemoryDraft:
    payload: Payload
    serialized: str
    source_id: str
    through_id: int
    publication: tuple


def _owner(claim: MemoryClaim) -> tuple[str, str, float, str]:
    return claim.chat_id, claim.session_id, claim.session_created_at, claim.layer


def _publication_snapshot(db: sqlite3.Connection, claim: MemoryClaim) -> tuple:
    """Fence concurrent accepted/manual edits, including clear-with-no-row."""
    owner = (claim.chat_id, claim.session_id)
    if claim.layer in {"summary", "scene"}:
        query = (
            "SELECT * FROM session_summaries WHERE chat_id=? AND session_id=?"
            if claim.layer == "summary"
            else "SELECT * FROM scene_states WHERE chat_id=? AND session_id=?"
        )
        parent = db.execute(query, owner).fetchone()
        classification = db.execute(
            "SELECT * FROM memory_artifact_visibility WHERE chat_id=? AND session_id=? AND artifact_kind=?",
            (*owner, claim.layer),
        ).fetchone()
        return parent, classification
    if claim.layer == "curator":
        key = f"memory_curator:{claim.chat_id}:{claim.session_id}"
        revision = f"memory_curator_revision:{claim.chat_id}:{claim.session_id}"
        return tuple(db.execute("SELECT key,value FROM meta WHERE key IN (?,?) ORDER BY key", (key, revision)))
    entities = tuple(db.execute("SELECT * FROM npc_entities WHERE chat_id=? AND session_id=? ORDER BY npc_id", owner))
    fields = tuple(
        db.execute(
            "SELECT f.* FROM npc_fields f JOIN npc_entities n ON n.npc_id=f.npc_id "
            "WHERE n.chat_id=? AND n.session_id=? ORDER BY f.npc_id,f.field_key",
            owner,
        )
    )
    coverage = db.execute("SELECT * FROM npc_extraction_state WHERE chat_id=? AND session_id=?", owner).fetchone()
    return entities, fields, coverage, simulation_revision(db, *owner)


def load_draft(db: sqlite3.Connection, claim: MemoryClaim) -> MemoryDraft:
    row = db.execute(
        "SELECT draft_json,draft_source_id,covered_id FROM memory_layer_state "
        "WHERE chat_id=? AND session_id=? AND session_created_at=? AND layer=?",
        _owner(claim),
    ).fetchone()
    if row is None:
        raise ValueError("Missing derived layer state")
    payload = json.loads(row[0]) if row[0] else {}
    if not isinstance(payload, dict):
        raise ValueError("Invalid derived draft")
    return MemoryDraft(payload, str(row[0]), str(row[1]), int(row[2]), _publication_snapshot(db, claim))


def _serialize(payload: Payload) -> str:
    value = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if len(value.encode("utf-8")) > MAX_DRAFT_BYTES:
        raise ValueError("Derived draft exceeds its bounded checkpoint allocation")
    return value


def prepare_draft(
    db: sqlite3.Connection,
    claim: MemoryClaim,
    *,
    valid: Callable[[], bool],
    restore: Callable[[Payload, int], None],
) -> MemoryDraft | None:
    with write_transaction(db):
        if not valid():
            return None
        draft = load_draft(db, claim)
        if draft.serialized:
            return draft
        floor = int(
            db.execute(
                "SELECT source_floor_id FROM memory_layer_state WHERE chat_id=? AND session_id=? "
                "AND session_created_at=? AND layer=?",
                _owner(claim),
            ).fetchone()[0]
        )
        payload: Payload = {}
        through, source_id = floor, ""
        for row in db.execute(
            "SELECT through_id,source_document_id,payload_json FROM memory_layer_checkpoints "
            "WHERE chat_id=? AND session_id=? AND session_created_at=? AND layer=? "
            "AND through_id>=? AND through_id<=? ORDER BY through_id DESC LIMIT ?",
            (*_owner(claim), floor, claim.target_id, MAX_CHECKPOINTS),
        ).fetchall():
            source = load_source(db, str(row[1]))
            if source is not None and source_is_valid(db, source):
                through, source_id, payload = int(row[0]), str(row[1]), json.loads(row[2])
                break
        # A cleared accumulator cannot skip archived prefix parts. Either restore
        # a proven complete prefix or replay all required parts from the floor.
        db.execute(
            "UPDATE memory_segments SET valid=0 WHERE chat_id=? AND session_id=? AND session_created_at=? "
            "AND layer=? AND end_id>?",
            (*_owner(claim), through),
        )
        restore(payload, through)
        serialized = _serialize(payload)
        db.execute(
            "UPDATE memory_layer_state SET draft_json=?,draft_source_id=?,covered_id=? "
            "WHERE chat_id=? AND session_id=? AND session_created_at=? AND layer=?",
            (serialized, source_id, through, *_owner(claim)),
        )
        return load_draft(db, claim)


def accept_draft_part(
    db: sqlite3.Connection,
    claim: MemoryClaim,
    source: MemorySource,
    previous: MemoryDraft,
    payload: Payload,
    *,
    valid: Callable[[], bool],
    publish: Callable[[Payload, int], None],
    completed_payload: Payload | None = None,
) -> bool:
    serialized = _serialize(payload)
    with write_transaction(db):
        if not valid() or not source_is_valid(db, source) or load_draft(db, claim) != previous:
            return False
        expected = next_source_segment(db, claim.chat_id, claim.session_id, claim.layer, through_id=claim.target_id)
        if expected != source:
            return False
        row_length = int(db.execute("SELECT length(content) FROM messages WHERE id=?", (source.end_id,)).fetchone()[0])
        complete = source.end_offset == row_length
        if not store_segment(db, source, advance_coverage=False):
            return False
        if complete:
            publish(payload, source.end_id)
            if completed_payload is not None:
                serialized = _serialize(completed_payload)
        db.execute(
            "UPDATE memory_layer_state SET draft_json=?,draft_source_id=?,covered_id=? "
            "WHERE chat_id=? AND session_id=? AND session_created_at=? AND layer=?",
            (serialized, source.document_id, source.end_id if complete else previous.through_id, *_owner(claim)),
        )
        if complete:
            db.execute(
                "INSERT OR REPLACE INTO memory_layer_checkpoints VALUES(?,?,?,?,?,?,?)",
                (*_owner(claim), source.end_id, source.document_id, serialized),
            )
            db.execute(
                "DELETE FROM memory_layer_checkpoints WHERE chat_id=? AND session_id=? AND session_created_at=? "
                "AND layer=? AND through_id NOT IN (SELECT through_id FROM memory_layer_checkpoints "
                "WHERE chat_id=? AND session_id=? AND session_created_at=? AND layer=? ORDER BY "
                "through_id DESC LIMIT ?)",
                (*_owner(claim), *_owner(claim), MAX_CHECKPOINTS),
            )
        return True


def process_draft_parts(
    db: sqlite3.Connection,
    claim: MemoryClaim,
    *,
    extract: Callable[[Payload, MemorySource], Payload],
    publish: Callable[[Payload, int], None],
    restore: Callable[[Payload, int], None],
    valid: Callable[[], bool],
    max_parts: int = 8,
    completed_payload: Payload | None = None,
    on_extract_error: Callable[[Exception, MemorySource], None] | None = None,
) -> str:
    if db.in_transaction:
        raise RuntimeError("Derived inference requires committed source")
    draft = prepare_draft(db, claim, valid=valid, restore=restore)
    if draft is None:
        return "stale_source"
    for _ in range(min(8, max(1, max_parts))):
        source = next_source_segment(db, claim.chat_id, claim.session_id, claim.layer, through_id=claim.target_id)
        if source is None:
            return "complete"
        with write_transaction(db):
            if not valid() or not source_is_valid(db, source):
                return "stale_source"
            db.execute(
                "UPDATE memory_jobs SET lease_deadline=? WHERE chat_id=? AND session_id=? "
                "AND session_created_at=? AND layer=? AND lease_token=? AND claimed_version=?",
                (time.time() + LEASE_SECONDS, *_owner(claim), claim.token, claim.version),
            )
        try:
            payload = extract(draft.payload, source)
        except Exception as exc:
            if on_extract_error is not None:
                with write_transaction(db):
                    if valid() and source_is_valid(db, source):
                        on_extract_error(exc, source)
            raise
        if not accept_draft_part(
            db, claim, source, draft, payload, valid=valid, publish=publish, completed_payload=completed_payload
        ):
            return "stale_source"
        draft = load_draft(db, claim)
    return (
        "complete"
        if next_source_segment(db, claim.chat_id, claim.session_id, claim.layer, through_id=claim.target_id) is None
        else "deferred"
    )


def run_session_draft(
    db: sqlite3.Connection,
    chat_id: str,
    session_id: str,
    layer: str,
    *,
    extract: Callable[[Payload, MemorySource], Payload],
    publish: Callable[[Payload, int], None],
    restore: Callable[[Payload, int], None],
    permitted: Callable[[], bool] = lambda: True,
    through_id: int | None = None,
    max_parts: int = 8,
    rebuild: bool = False,
    completed_payload: Payload | None = None,
    on_extract_error: Callable[[Exception, MemorySource], None] | None = None,
) -> str:
    """Manual entry points reuse the same bounded durable claims and accumulator."""
    from dataclasses import replace

    from bridge.memory_store import acknowledge_job, claim_is_current, claim_jobs, enqueue_memory, fail_job

    if db.in_transaction:
        raise RuntimeError("Derived inference requires committed source")
    with write_transaction(db):
        row = db.execute(
            "SELECT dirty_version>completed_version,lease_token FROM memory_jobs "
            "WHERE chat_id=? AND session_id=? AND layer=?",
            (chat_id, session_id, layer),
        ).fetchone()
        if row is None:
            enqueue_memory(db, chat_id, session_id, layer)
        elif rebuild and not row[0] and not row[1]:
            db.execute(
                "UPDATE memory_layer_state SET covered_id=source_floor_id,rewrite_identity=rewrite_identity+1,"
                "invalidated_from_id=source_floor_id+1 WHERE chat_id=? AND session_id=? AND layer=?",
                (chat_id, session_id, layer),
            )
            enqueue_memory(db, chat_id, session_id, layer)
        elif not row[0]:
            return "complete"
    if not permitted():
        return "disabled"
    claims = claim_jobs(db, layers=(layer,), chat_id=chat_id, session_id=session_id)
    if not claims:
        return "deferred"
    original = claims[0]
    claim = (
        replace(original, target_id=min(original.target_id, max(0, through_id))) if through_id is not None else original
    )
    try:
        result = process_draft_parts(
            db,
            claim,
            extract=extract,
            publish=publish,
            restore=restore,
            valid=lambda: claim_is_current(db, claim) and permitted(),
            max_parts=max_parts,
            completed_payload=completed_payload,
            on_extract_error=on_extract_error,
        )
        if result == "complete" and claim.target_id == original.target_id:
            if not acknowledge_job(db, original):
                return "stale_source"
        else:
            fail_job(
                db,
                original,
                "deferred" if result == "complete" else result,
                deferred=result in {"complete", "deferred"},
            )
        return result
    except Exception:
        fail_job(db, original, "work_failed")
        raise
