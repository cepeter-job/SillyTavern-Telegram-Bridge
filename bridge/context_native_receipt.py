"""Native SQLite evidence-retention receipts; no automatic semantic/rollout authority.

Exact reconstruction retains every fact/causal statement in its original source,
role and occurrence. This is not a claim about an LLM's interpretation of a codec.
No source text is returned in diagnostics and no database writes are performed.
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from dataclasses import dataclass, field

from bridge.context_history_codec import HISTORY_INDEX, MAX_TURNS, unpack_history
from bridge.memory_artifact_store import load_artifact_classification
from bridge.memory_contracts import MemoryReadScope
from bridge.memory_fact_store import load_source
from bridge.memory_scope_store import resolve_memory_scope
from bridge.memory_store import source_is_valid
from bridge.user_dialogue import format_user_dialogue_action


@dataclass(frozen=True)
class NativeHistoryReceipt:
    scope: MemoryReadScope = field(repr=False)
    baseline_sha256: str
    source_fingerprints: tuple[tuple[int, str, float, str], ...] = field(repr=False)
    coverage_sha256: str


def _hash(value: object) -> str:
    data = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True, allow_nan=False)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def _prompt_hash(messages: list[dict]) -> str:
    return _hash(
        [{key: message[key] for key in ("role", "content", HISTORY_INDEX) if key in message} for message in messages]
    )


def _current_scope(db: sqlite3.Connection, scope: MemoryReadScope) -> None:
    if (
        not isinstance(scope, MemoryReadScope)
        or scope.historical
        or not scope.chat_id
        or not scope.session_id
        or not math.isfinite(scope.session_created_at)
        or scope.session_created_at <= 0
        or scope.consumer not in {"character", "narrator"}
        or not scope.principals
        or scope.through_rowid <= 0
    ):
        raise ValueError("native_scope_invalid")
    actual = resolve_memory_scope(
        db,
        scope.chat_id,
        {"session_id": scope.session_id},
        {},
        principals=scope.principals,
        consumer=scope.consumer,
    )
    if actual != scope:
        raise ValueError("native_scope_changed")


def _accepted_coverage(db: sqlite3.Connection, scope: MemoryReadScope) -> tuple:
    row = db.execute(
        "SELECT l.covered_id,l.rewrite_identity,l.purge_epoch,l.invalidated_from_id,l.source_floor_id,"
        "j.target_id,j.dirty_version,j.completed_version,j.lease_token "
        "FROM memory_layer_state l JOIN memory_jobs j ON j.chat_id=l.chat_id AND j.session_id=l.session_id "
        "AND j.session_created_at=l.session_created_at AND j.layer=l.layer "
        "WHERE l.chat_id=? AND l.session_id=? AND l.session_created_at=? AND l.layer='summary'",
        (scope.chat_id, scope.session_id, scope.session_created_at),
    ).fetchone()
    if (
        row is None
        or row[0] < scope.through_rowid
        or row[3] is not None
        or row[5] > row[0]
        or row[6] != row[7]
        or row[8]
    ):
        raise ValueError("native_summary_not_current")
    classification = load_artifact_classification(db, scope.chat_id, scope.session_id, "summary")
    if classification is None or classification[1] != row[0]:
        raise ValueError("native_summary_unclassified")
    checkpoint = db.execute(
        "SELECT source_document_id FROM memory_layer_checkpoints WHERE chat_id=? AND session_id=? "
        "AND session_created_at=? AND layer='summary' AND through_id=?",
        (scope.chat_id, scope.session_id, scope.session_created_at, row[0]),
    ).fetchone()
    source = load_source(db, checkpoint[0]) if checkpoint else None
    if (
        source is None
        or not source_is_valid(db, source)
        or source.layer != "summary"
        or (source.chat_id, source.session_id, source.session_created_at, source.end_id)
        != (scope.chat_id, scope.session_id, scope.session_created_at, row[0])
    ):
        raise ValueError("native_checkpoint_missing")
    return (*row, _hash(classification), checkpoint[0])


def _row_proof(db: sqlite3.Connection, scope: MemoryReadScope, row: tuple) -> tuple[str, ...]:
    rowid, role, text, _created = row
    segments = db.execute(
        "SELECT document_id FROM memory_segments WHERE chat_id=? AND session_id=? AND session_created_at=? "
        "AND layer='summary' AND start_id=? AND end_id=? AND valid=1 ORDER BY start_offset,end_offset,document_id",
        (scope.chat_id, scope.session_id, scope.session_created_at, rowid, rowid),
    ).fetchall()
    offset = 0
    receipts: list[str] = []
    for (document_id,) in segments:
        source = load_source(db, document_id)
        if (
            source is None
            or not source_is_valid(db, source)
            or (source.chat_id, source.session_id, source.session_created_at, source.layer)
            != (scope.chat_id, scope.session_id, scope.session_created_at, "summary")
            or source.role != role
            or source.start_id != rowid
            or source.end_id != rowid
            or not 0 <= source.start_offset <= offset <= len(text)
            or not source.start_offset <= source.end_offset <= len(text)
            or source.content != text[source.start_offset : source.end_offset]
        ):
            raise ValueError("native_source_interval_invalid")
        offset = max(offset, source.end_offset)
        receipts.append(document_id)
    if not receipts or offset != len(text):
        raise ValueError("native_source_coverage_gap")
    return tuple(receipts)


def capture_native_history(
    db: sqlite3.Connection, scope: MemoryReadScope, messages: list[dict]
) -> NativeHistoryReceipt:
    """Bind a baseline's exact historical suffix to current accepted native rows.

    Callers must supply the same authenticated reader scope used to build their
    baseline. This receipt grants no extra audience; it only prevents changing it.
    A caller-supplied manifest, summary or boolean cannot substitute for SQLite.
    """
    _current_scope(db, scope)
    # Validating a plain baseline through the decoder rejects codec metadata,
    # malformed roles/order and expansion limits before any source query.
    if unpack_history(messages) != messages:
        raise ValueError("native_baseline_already_encoded")
    historical = [message for message in messages if HISTORY_INDEX in message]
    if not 1 <= len(historical) <= MAX_TURNS:
        raise ValueError("native_history_count")
    coverage = _accepted_coverage(db, scope)
    rows = tuple(
        reversed(
            db.execute(
                "SELECT id,role,content,created_at FROM messages WHERE chat_id=? AND session_id=? "
                "AND id<=? ORDER BY created_at DESC,id DESC LIMIT ?",
                (scope.chat_id, scope.session_id, scope.through_rowid, len(historical)),
            ).fetchall()
        )
    )
    if len(rows) != len(historical):
        raise ValueError("native_history_missing")
    source_proofs = []
    fingerprints = []
    for ordinal, (row, message) in enumerate(zip(rows, historical, strict=True)):
        rowid, role, text, created = row
        expected = format_user_dialogue_action(text) if role == "user" else text
        if (
            type(message.get(HISTORY_INDEX)) is not int
            or message[HISTORY_INDEX] != ordinal
            or message.get("role") != role
            or message.get("content") != expected
            or created < scope.session_created_at
            or rowid <= coverage[4]
            or not math.isfinite(created)
        ):
            raise ValueError("native_baseline_source_mismatch")
        source_proofs.append(_row_proof(db, scope, row))
        fingerprints.append((rowid, role, created, _hash(text)))
    _current_scope(db, scope)
    if _accepted_coverage(db, scope) != coverage:
        raise ValueError("native_coverage_changed")
    return NativeHistoryReceipt(scope, _prompt_hash(messages), tuple(fingerprints), _hash((coverage, source_proofs)))


def verify_native_candidate(
    db: sqlite3.Connection,
    receipt: NativeHistoryReceipt,
    baseline: list[dict],
    candidate: list[dict],
    *,
    current_scope: MemoryReadScope,
) -> dict[str, object]:
    """Revalidate source ownership and exact reconstruction at the dispatch boundary.

    This constructive source-retention proof preserves *all* causal evidence,
    including unrecognized facts, rather than trusting a model-generated list
    to enumerate the facts worth retaining. Semantic/rollout approval stays false.
    """
    result: dict[str, object] = {
        "schema_version": 1,
        "native_source_verified": False,
        "all_source_evidence_preserved": False,
        "role_order_multiplicity_preserved": False,
        "checked_source_rows": 0,
        "semantic_equivalence_proven": False,
        "production_activation_allowed": False,
        "authority": "native_sqlite_exact_source_reconstruction",
        "reason_codes": [],
    }
    if not isinstance(receipt, NativeHistoryReceipt):
        result["reason_codes"] = ["invalid_native_receipt"]
        return result
    try:
        if receipt.scope != current_scope:
            raise ValueError("native_reader_or_scope_changed")
        fresh = capture_native_history(db, current_scope, baseline)
        if fresh != receipt:
            raise ValueError("native_receipt_changed")
        if unpack_history(candidate) != baseline:
            raise ValueError("native_reconstruction_mismatch")
        result.update(
            native_source_verified=True,
            all_source_evidence_preserved=True,
            role_order_multiplicity_preserved=True,
            checked_source_rows=len(receipt.source_fingerprints),
        )
    except (ValueError, TypeError, KeyError, sqlite3.Error):
        # Content-free diagnostics, including failures caused by corrupt SQLite.
        result["reason_codes"] = ["native_continuity_verification_failed"]
    return result
