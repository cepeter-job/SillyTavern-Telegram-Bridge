"""Immutable pre-finale checkpoints and atomic entry into the ending path."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
from dataclasses import dataclass, replace
from typing import Any

from bridge.ending_service import ending_source, load_ending_state, publish_ending_state, require_current_ending_facts
from bridge.narrative_checkpoint_capture import capture_pre_finale_state
from bridge.narrative_checkpoint_repository import insert_narrative_checkpoint, load_pre_finale_checkpoint_row
from bridge.narrative_settings import load_session_narrative_settings
from bridge.sqlite_store import write_transaction
from bridge.transcript_repository import transcript_prefix_fingerprint


@dataclass(frozen=True, slots=True)
class NarrativeCheckpoint:
    checkpoint_id: str
    source_revision: int
    through_rowid: int
    payload: dict[str, Any]


def _decode(row: dict[str, Any]) -> NarrativeCheckpoint:
    if row["format_version"] != 1 or len(str(row["payload_json"]).encode()) > 1048576:
        raise ValueError("The saved pre-finale checkpoint version or size is unsupported")
    try:
        payload = json.loads(row["payload_json"])
    except (ValueError, TypeError, RecursionError) as exc:
        raise ValueError("The pre-finale checkpoint is unreadable") from exc
    required = {"origin", "state", "ending", "settings", "session", "config", "memory", "director", "transcript"}
    if (
        not isinstance(payload, dict)
        or payload.get("format_version") != 1
        or not required <= payload.keys()
        or not all(isinstance(payload[key], dict) for key in required)
    ):
        raise ValueError("The pre-finale checkpoint is incomplete")
    return NarrativeCheckpoint(row["checkpoint_id"], row["source_revision"], row["through_rowid"], payload)


def load_pre_finale_checkpoint(db: sqlite3.Connection, chat_id: str, session_id: str) -> NarrativeCheckpoint | None:
    identifier = load_ending_state(db, chat_id, session_id).checkpoint_id
    if not identifier:
        return None
    row = load_pre_finale_checkpoint_row(db, chat_id, session_id, identifier)
    return _decode(row) if row is not None else None


def validate_pre_finale_checkpoint(
    db: sqlite3.Connection, chat_id: str, session_id: str, checkpoint_id: str
) -> NarrativeCheckpoint:
    ending = load_ending_state(db, chat_id, session_id)
    if ending.checkpoint_id != checkpoint_id:
        raise ValueError("That pre-finale checkpoint is not owned by this story's ending")
    row = load_pre_finale_checkpoint_row(db, chat_id, session_id, checkpoint_id)
    if row is None:
        raise ValueError("This story has no valid pre-finale checkpoint")
    cp = _decode(row)
    if cp.payload["origin"] != {"chat_id": chat_id, "session_id": session_id}:
        raise ValueError("The pre-finale checkpoint belongs to another story")
    if (
        cp.payload["state"].get("updated_through_rowid") != cp.through_rowid
        or cp.payload["state"].get("state_revision") != cp.source_revision
    ):
        raise ValueError("The pre-finale checkpoint boundary is inconsistent")
    if cp.payload["transcript"] != transcript_prefix_fingerprint(db, chat_id, session_id, cp.through_rowid):
        raise ValueError("The saved pre-finale transcript prefix no longer matches its checkpoint")
    return cp


def create_pre_finale_checkpoint_and_enter(
    db: sqlite3.Connection,
    chat_id: str,
    session_id: str,
    *,
    expected_story_revision: int,
    expected_lifecycle_revision: int,
    operation_id: str,
) -> NarrativeCheckpoint:
    if not isinstance(operation_id, str) or not re.fullmatch(r"[A-Za-z0-9:_-]{1,100}", operation_id):
        raise ValueError("A valid finale operation identity is required")
    with write_transaction(db):
        current = load_ending_state(db, chat_id, session_id)
        if current.lifecycle != "finale_ready":
            if current.lifecycle == "finale" and current.finale_operation_id == operation_id:
                cp = load_pre_finale_checkpoint(db, chat_id, session_id)
                if cp is not None:
                    return cp
            raise ValueError("The finale is not ready or another finale operation already started it")
        current, clock = ending_source(
            db,
            chat_id,
            session_id,
            story_revision=expected_story_revision,
            expected_lifecycle_revision=expected_lifecycle_revision,
            expected_goal_revision=current.goal_revision,
        )
        require_current_ending_facts(clock)
        if (
            current.finale_ready_revision != clock["state_revision"]
            or current.ready_history_revision != clock["history_revision"]
            or current.ready_settings_revision != clock["settings_revision"]
            or current.ready_rewrite_revision != clock["rewrite_revision"]
            or current.ready_through_rowid != clock["latest_rowid"]
            or load_session_narrative_settings(db, chat_id, session_id).ending_mode != "closed_story"
        ):
            raise ValueError("Finale readiness changed. Reassess before starting the finale")
        payload = capture_pre_finale_state(db, chat_id, session_id, clock["latest_rowid"])
        fingerprint = json.dumps([chat_id, session_id, clock["state_revision"], current.goal_revision, operation_id])
        identifier = "prefinale_" + hashlib.sha256(fingerprint.encode()).hexdigest()
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        insert_narrative_checkpoint(
            db,
            chat_id,
            session_id,
            identifier,
            kind="pre_finale",
            source_revision=clock["state_revision"],
            through_rowid=clock["latest_rowid"],
            payload_json=encoded,
            created_at=time.time(),
        )
        publish_ending_state(
            db,
            chat_id,
            session_id,
            current,
            replace(
                current,
                lifecycle="finale",
                checkpoint_id=identifier,
                finale_operation_id=operation_id,
            ),
        )
        row = load_pre_finale_checkpoint_row(db, chat_id, session_id, identifier)
        if row is None:
            raise RuntimeError("The atomic pre-finale checkpoint was not stored")
        return _decode(row)
