"""Atomic Summary ACK proof from accepted canonical source artifacts.

Coverage numbers alone are not source proof. No provider requests are made.
"""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bridge.memory_store import MemoryClaim


def summary_ack_has_accepted_source(db: sqlite3.Connection, claim: MemoryClaim) -> bool:
    """Reject source-pointer-only completion, invalid segments and stale audiences."""
    # Local imports avoid store/publisher import cycles.
    from bridge.memory_artifact_store import load_artifact_classification
    from bridge.memory_fact_store import load_source
    from bridge.memory_store import source_is_valid

    owner = (claim.chat_id, claim.session_id, claim.session_created_at, "summary")
    state = db.execute(
        "SELECT covered_id,source_floor_id,rewrite_identity,purge_epoch,draft_json,draft_source_id "
        "FROM memory_layer_state WHERE chat_id=? AND session_id=? AND session_created_at=? AND layer=?",
        owner,
    ).fetchone()
    if state is None:
        return False
    covered, floor, rewrite, purge, draft, draft_source = state
    if floor > covered or covered > claim.target_id:
        return False
    source_rows = db.execute(
        "SELECT id,length(content) FROM messages WHERE chat_id=? AND session_id=? AND id>? AND id<=? ORDER BY id",
        (claim.chat_id, claim.session_id, floor, claim.target_id),
    ).fetchall()
    if not source_rows:
        # Empty/deleted-story completion cannot publish stale classified facts.
        return load_artifact_classification(db, claim.chat_id, claim.session_id, "summary") is None
    if covered != source_rows[-1][0]:
        return False

    checkpoint = db.execute(
        "SELECT source_document_id,payload_json FROM memory_layer_checkpoints "
        "WHERE chat_id=? AND session_id=? AND session_created_at=? AND layer=? AND through_id=?",
        (*owner, covered),
    ).fetchone()
    if checkpoint is None or not draft or draft != checkpoint[1] or draft_source != checkpoint[0]:
        return False
    classification = load_artifact_classification(db, claim.chat_id, claim.session_id, "summary")
    if classification is None or classification[1] != covered:
        return False
    last_part = load_source(db, checkpoint[0])
    if (
        last_part is None
        or last_part.end_id != covered
        or last_part.rewrite_identity != rewrite
        or last_part.purge_epoch != purge
        or not source_is_valid(db, last_part)
    ):
        return False

    for rowid, length in source_rows:
        cursor = 0
        parts = db.execute(
            "SELECT document_id,start_offset,end_offset FROM memory_segments "
            "WHERE chat_id=? AND session_id=? AND session_created_at=? AND layer='summary' "
            "AND start_id=? AND end_id=? AND valid=1 AND rewrite_identity=? AND purge_epoch=? "
            "ORDER BY start_offset,end_offset",
            (claim.chat_id, claim.session_id, claim.session_created_at, rowid, rowid, rewrite, purge),
        )
        try:
            seen = False
            for document_id, start, end in parts:
                source = load_source(db, document_id)
                if (
                    start != cursor
                    or end < start
                    or end > length
                    or (end == start and length != 0)
                    or source is None
                    or source.rewrite_identity != rewrite
                    or source.purge_epoch != purge
                    or not source_is_valid(db, source)
                ):
                    return False
                seen = True
                cursor = end
            if not seen or cursor != length:
                return False
        finally:
            parts.close()
    return True
