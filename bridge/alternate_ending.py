"""Create independent alternate endings without mutating a completed original."""

from __future__ import annotations

import hashlib
import logging
import re
import secrets
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass

from bridge.alternate_ending_repository import (
    activate_branch_if_origin,
    branch_for_operation,
    finish_branch_memory,
    record_branch,
)
from bridge.alternate_ending_restore import restore_checkpoint_state
from bridge.checkpoint_remap import checkpoint_row_references
from bridge.ending_service import load_ending_state
from bridge.narrative_checkpoints import validate_pre_finale_checkpoint
from bridge.operation_repository import (
    claim_scoped_operation_stage,
    finish_scoped_operation,
    prepare_scoped_operation,
    read_scoped_operation,
    write_operation_phase,
)
from bridge.session_repository import insert_session_row, load_session_row
from bridge.session_titles import normalize_session_title
from bridge.sqlite_store import write_transaction
from bridge.transcript_repository import clone_transcript_prefix

SeedMemory = Callable[[sqlite3.Connection, str, dict[str, str]], str]


@dataclass(frozen=True)
class AlternateEndingResult:
    session: dict[str, str]
    applied: bool
    memory_status: str


def alternate_ending_target_id(chat_id: str, origin: str, checkpoint: str, operation_id: str) -> str:
    identity = "\0".join((chat_id, origin, checkpoint, operation_id))
    return "alternate-" + hashlib.sha256(identity.encode()).hexdigest()[:32]


def _operation_key(chat_id: str, operation_id: str) -> str:
    if not isinstance(operation_id, str) or not re.fullmatch(r"[A-Za-z0-9:_-]{1,100}", operation_id):
        raise ValueError("An explicit alternate-ending operation identity is required")
    return "alternate-ending-" + hashlib.sha256((chat_id + "\0" + operation_id).encode()).hexdigest()


def _result(db: sqlite3.Connection, operation_id: str) -> AlternateEndingResult:
    lineage = branch_for_operation(db, operation_id)
    if not lineage:
        raise ValueError("This alternate-ending operation has no committed target")
    target = load_session_row(db, lineage["chat_id"], lineage["target_session_id"])
    if target is None:
        raise ValueError("The alternate-ending target was deleted; it will not be recreated")
    return AlternateEndingResult(target, lineage["memory_status"] != "pending", lineage["memory_status"])


def finish_alternate_ending(
    db: sqlite3.Connection,
    operation_id: str,
    *,
    seed_memory: SeedMemory | None = None,
) -> AlternateEndingResult:
    if db.in_transaction:
        raise ValueError("External branch-memory work cannot run inside a database transaction")
    current = _result(db, operation_id)
    if current.applied:
        return current
    token = secrets.token_hex(16)
    with write_transaction(db):
        claimed = claim_scoped_operation_stage(
            db, operation_id, token, time.time(), stage="memory_seeding", prior="local_committed"
        )
    if not claimed:
        return current
    lineage = branch_for_operation(db, operation_id)
    if lineage is None:
        raise ValueError("The alternate-ending target was removed")
    try:
        status = seed_memory(db, lineage["chat_id"], current.session) if seed_memory else "disabled"
        if status not in {"disabled", "ready", "degraded"}:
            status = "degraded"
    except Exception as exc:
        logging.warning("Alternate ending is usable; optional memory seeding failed: %s", type(exc).__name__)
        status = "degraded"
    with write_transaction(db):
        if finish_scoped_operation(db, operation_id, token, time.time()):
            finish_branch_memory(db, operation_id, status)
            activate_branch_if_origin(
                db, lineage["chat_id"], lineage["origin_session_id"], lineage["target_session_id"]
            )
    return _result(db, operation_id)


def create_alternate_ending(
    db: sqlite3.Connection,
    chat_id: str,
    origin_session_id: str,
    checkpoint_id: str,
    operation_id: str,
    *,
    title: str | None = None,
    seed_memory: SeedMemory | None = None,
) -> AlternateEndingResult:
    if db.in_transaction:
        raise ValueError("Alternate-ending creation must own its local transaction")
    key = _operation_key(chat_id, operation_id)
    existing = branch_for_operation(db, key)
    if existing:
        if (existing["chat_id"], existing["origin_session_id"], existing["checkpoint_id"]) != (
            chat_id,
            origin_session_id,
            checkpoint_id,
        ):
            raise ValueError("This operation belongs to another checkpoint")
        return finish_alternate_ending(db, key, seed_memory=seed_memory)
    old = read_scoped_operation(db, key)
    if old is not None and old["state"] == "applied":
        raise ValueError("This operation already completed and its target was deleted")
    if load_ending_state(db, chat_id, origin_session_id).lifecycle != "closed":
        raise ValueError("Alternate Ending requires a completed original story")
    checkpoint = validate_pre_finale_checkpoint(db, chat_id, origin_session_id, checkpoint_id)
    target = alternate_ending_target_id(chat_id, origin_session_id, checkpoint_id, operation_id)
    source = checkpoint.payload["session"]
    target_title = (
        normalize_session_title(title)
        if title is not None
        else normalize_session_title((str(source.get("title") or "Story")[:61]).rstrip() + " — Alternate Ending")
    )
    payload = {
        "chat_id": chat_id,
        "origin_session_id": origin_session_id,
        "target_session_id": target,
        "checkpoint_id": checkpoint_id,
        "checkpoint_revision": checkpoint.source_revision,
        "title": target_title,
    }
    with write_transaction(db):
        prepare_scoped_operation(db, key, "alternate_ending", payload, time.time())
    with write_transaction(db):
        # The admission and source check are repeated under the writer lock.
        if branch_for_operation(db, key) is None:
            if load_ending_state(db, chat_id, origin_session_id).lifecycle != "closed":
                raise ValueError("The original is no longer a completed story")
            checkpoint = validate_pre_finale_checkpoint(db, chat_id, origin_session_id, checkpoint_id)
            if load_session_row(db, chat_id, target) is not None:
                raise ValueError("An alternate-ending target identity already exists")
            values = dict(source, chat_id=chat_id, session_id=target, title=target_title)
            insert_session_row(db, values, time.time())
            refs = checkpoint_row_references(checkpoint.payload) | {checkpoint.through_rowid}
            rowids = clone_transcript_prefix(db, chat_id, origin_session_id, target, checkpoint.through_rowid, refs)
            restore_checkpoint_state(db, chat_id, target, checkpoint, rowids)
            record_branch(
                db, chat_id, target, origin_session_id, checkpoint_id, checkpoint.source_revision, key, time.time()
            )
            write_operation_phase(db, key, "alternate_ending", "local_committed", time.time())
    return finish_alternate_ending(db, key, seed_memory=seed_memory)
