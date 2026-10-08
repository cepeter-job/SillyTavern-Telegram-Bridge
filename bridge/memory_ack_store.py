"""Atomically ACK durable jobs; Summary needs complete accepted source evidence.

This module is below the memory-store/publisher dependency boundary. It
rechecks canonical source and classified publication inside the ACK transaction
without model calls or exporting dialogue, identifiers, or classifications.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Protocol

from bridge.memory_queue import ACK_GUARD
from bridge.sqlite_store import write_transaction


class MemoryClaim(Protocol):
    @property
    def chat_id(self) -> str: ...

    @property
    def session_id(self) -> str: ...

    @property
    def session_created_at(self) -> float: ...

    @property
    def layer(self) -> str: ...

    @property
    def token(self) -> str: ...

    @property
    def version(self) -> int: ...

    @property
    def target_id(self) -> int: ...

    @property
    def rewrite_identity(self) -> int: ...

    @property
    def purge_epoch(self) -> int: ...


def _digest(role: str, content: str) -> str:
    return hashlib.sha256(json.dumps([role, content], ensure_ascii=False).encode()).hexdigest()


def _classified_publication(db: sqlite3.Connection, claim: MemoryClaim, covered: int) -> bool:
    row = db.execute(
        "SELECT summary,covered_until_rowid FROM session_summaries WHERE chat_id=? AND session_id=?",
        (claim.chat_id, claim.session_id),
    ).fetchone()
    sidecar = db.execute(
        "SELECT payload_digest,through_rowid,blocks_json FROM memory_artifact_visibility "
        "WHERE chat_id=? AND session_id=? AND session_created_at=? "
        "AND artifact_kind='summary' AND schema_version=1",
        (claim.chat_id, claim.session_id, claim.session_created_at),
    ).fetchone()
    if row is None or sidecar is None or row[1] != covered or sidecar[1] != covered:
        return False
    if hashlib.sha256(json.dumps(row[0], ensure_ascii=False, sort_keys=True).encode()).hexdigest() != sidecar[0]:
        return False
    try:
        blocks = json.loads(sidecar[2])
    except (TypeError, ValueError):
        return False
    if not isinstance(blocks, list) or len(blocks) > 32:
        return False
    lines: list[str] = []
    for block in blocks:
        if not isinstance(block, dict):
            return False
        text = block.get("text")
        visibility = block.get("visibility")
        known_by = block.get("known_by")
        if (
            not isinstance(text, str)
            or not text.strip()
            or len(text) > 5000
            or visibility not in {"shared", "restricted"}
            or not isinstance(known_by, list)
            or any(not isinstance(name, str) or not name.strip() for name in known_by)
            or (visibility == "shared" and bool(known_by))
            or (visibility == "restricted" and not known_by)
        ):
            return False
        lines.append(text)
    return "\n".join(lines) == row[0]


def summary_ack_has_accepted_source(db: sqlite3.Connection, claim: MemoryClaim) -> bool:
    """Reject merely advanced coverage, changed source, or invalid publication."""
    owner = (claim.chat_id, claim.session_id, claim.session_created_at, "summary")
    state = db.execute(
        "SELECT l.covered_id,l.source_floor_id,l.rewrite_identity,l.purge_epoch,"
        "l.draft_json,l.draft_source_id FROM memory_layer_state l "
        "JOIN sessions s ON s.chat_id=l.chat_id AND s.session_id=l.session_id "
        "AND s.created_at=l.session_created_at "
        "JOIN memory_jobs j ON j.chat_id=l.chat_id AND j.session_id=l.session_id "
        "AND j.session_created_at=l.session_created_at AND j.layer=l.layer "
        "WHERE l.chat_id=? AND l.session_id=? AND l.session_created_at=? AND l.layer=? "
        "AND j.lease_token=? AND j.claimed_version=? "
        "AND l.rewrite_identity=? AND l.purge_epoch=?",
        (*owner, claim.token, claim.version, claim.rewrite_identity, claim.purge_epoch),
    ).fetchone()
    if state is None:
        return False
    covered, floor, _rewrite, purge, draft, draft_source = state
    if floor > covered or covered > claim.target_id:
        return False
    source_rows = db.execute(
        "SELECT id,role,content FROM messages WHERE chat_id=? AND session_id=? AND id>? AND id<=? ORDER BY id",
        (claim.chat_id, claim.session_id, floor, claim.target_id),
    ).fetchall()
    if not source_rows:
        # All source rows deleted/retired: require no classified publication.
        sidecar = db.execute(
            "SELECT 1 FROM memory_artifact_visibility WHERE chat_id=? AND session_id=? "
            "AND session_created_at=? AND artifact_kind='summary' LIMIT 1",
            owner[:3],
        ).fetchone()
        return sidecar is None
    if covered != source_rows[-1][0] or not _classified_publication(db, claim, covered):
        return False
    checkpoint = db.execute(
        "SELECT source_document_id,payload_json FROM memory_layer_checkpoints "
        "WHERE chat_id=? AND session_id=? AND session_created_at=? AND layer=? AND through_id=?",
        (*owner, covered),
    ).fetchone()
    if checkpoint is None or not draft or draft != checkpoint[1] or draft_source != checkpoint[0]:
        return False

    last_document = ""
    for rowid, role, text in source_rows:
        cursor = 0
        # A suffix rewrite preserves still-valid, previously accepted prefix
        # segments from an older revision; never require all prefix revisions
        # to equal the newest layer revision.
        parts = db.execute(
            "SELECT document_id,start_offset,end_offset,source_digest FROM memory_segments "
            "WHERE chat_id=? AND session_id=? AND session_created_at=? AND layer='summary' "
            "AND start_id=? AND end_id=? AND valid=1 AND purge_epoch=? "
            "ORDER BY start_offset,end_offset",
            (claim.chat_id, claim.session_id, claim.session_created_at, rowid, rowid, purge),
        )
        try:
            seen = False
            for document_id, start, end, digest in parts:
                if (
                    start != cursor
                    or end < start
                    or end > len(text)
                    or (end == start and text)
                    or _digest(role, text[start:end]) != digest
                ):
                    return False
                seen = True
                cursor = end
                last_document = document_id
            if not seen or cursor != len(text):
                return False
        finally:
            parts.close()
    return last_document == checkpoint[0]


def acknowledge_memory_job(db: sqlite3.Connection, claim: MemoryClaim) -> bool:
    """Preserve existing claim/lease/transaction fences for all durable layers."""
    scope = (claim.chat_id, claim.session_id, claim.session_created_at, claim.layer, claim.token, claim.version)
    with write_transaction(db):
        if claim.layer == "summary" and not summary_ack_has_accepted_source(db, claim):
            return False
        accepted = bool(
            db.execute(
                "UPDATE memory_jobs SET completed_version=?,lease_token='',lease_deadline=0,"  # noqa: S608 -- audited fixed internal SQL
                "attempts=0,last_error='',next_attempt_at=0 WHERE chat_id=? AND session_id=? "
                "AND session_created_at=? AND layer=? AND lease_token=? AND claimed_version=? "
                "AND EXISTS(SELECT 1 FROM memory_layer_state l WHERE l.chat_id=memory_jobs.chat_id "
                "AND l.session_id=memory_jobs.session_id AND l.session_created_at=memory_jobs.session_created_at "
                f"AND l.layer=memory_jobs.layer AND l.rewrite_identity=? AND l.purge_epoch=? {ACK_GUARD})",
                (claim.version, *scope, claim.rewrite_identity, claim.purge_epoch),
            ).rowcount
        )
        if accepted:
            db.execute(
                "UPDATE memory_layer_state SET invalidated_from_id=NULL WHERE chat_id=? AND session_id=? "
                "AND session_created_at=? AND layer=? AND rewrite_identity=? AND purge_epoch=?",
                (
                    claim.chat_id,
                    claim.session_id,
                    claim.session_created_at,
                    claim.layer,
                    claim.rewrite_identity,
                    claim.purge_epoch,
                ),
            )
        return accepted
