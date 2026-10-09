"""Exact-payload classification sidecars for independently read summary and scene."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from bridge.limits import SUMMARY_MAX_CHARS
from bridge.memory_contracts import MemoryBlock, MemoryBlockLeaf, MemoryEvidence, MemoryReadScope
from bridge.memory_fact_store import classified_audience, digest_value
from bridge.memory_store import request_source_cutoff
from bridge.narrative_repository import load_narrative_clock

CLASSIFIED_AUDIENCE_PROMPT = (
    'Audience contract: visibility="shared" requires known_by=[]; '
    'visibility="restricted" requires known_by to contain one or more actual character names. '
    "Never attach character names to a shared block."
)


def parse_classified_blocks(value: object) -> list[dict[str, Any]]:
    """Require explicit audiences; malformed model classifications fail closed."""
    if isinstance(value, dict):
        if "blocks" in value:
            value = value["blocks"]
        elif "summary" in value:
            value = [dict(value, text=value["summary"])]
    if not isinstance(value, list) or len(value) > 32:
        raise ValueError("Memory classification requires at most 32 explicit blocks")
    blocks: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict) or not isinstance(item.get("text"), str):
            raise ValueError("Classified memory blocks require text")
        text = item["text"].strip()
        if not text or len(text) > 5000:
            raise ValueError("Classified memory text must be bounded and nonempty")
        mode, names = classified_audience(item.get("visibility"), item.get("known_by"))
        blocks.append({"text": text, "visibility": mode, "known_by": list(names)})
    if sum(len(block["text"]) for block in blocks) > 32000:
        raise ValueError("Classified memory exceeds the artifact bound")
    return blocks


def parse_classified_response(raw: str) -> dict[str, Any]:
    text = raw.strip()
    if text.startswith(chr(96) * 3) and text.endswith(chr(96) * 3):
        text = text[3:-3].strip()
        if text.startswith("json"):
            text = text[4:].strip()
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise ValueError("Classified memory response must be a JSON object")
    return payload


def _artifact_payload(db: sqlite3.Connection, chat_id: str, session_id: str, kind: str) -> tuple[str, int] | None:
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
        raise ValueError("Unknown classified artifact kind")
    return (str(row[0]), int(row[1])) if row else None


def store_artifact_visibility(
    db: sqlite3.Connection, chat_id: str, session_id: str, kind: str, blocks: list[dict[str, Any]]
) -> None:
    if not db.in_transaction:
        raise RuntimeError("Classification must be accepted in the artifact write transaction")
    blocks = parse_classified_blocks(blocks)
    payload = _artifact_payload(db, chat_id, session_id, kind)
    clock = load_narrative_clock(db, chat_id, session_id)
    if payload is None or clock is None or payload[1] > int(clock["latest_rowid"]):
        raise ValueError("Classification requires the accepted canonical artifact boundary")
    db.execute(
        "INSERT OR REPLACE INTO memory_artifact_visibility VALUES(?,?,?,?,?,?,?,?,?)",
        (
            chat_id,
            session_id,
            clock["session_created_at"],
            kind,
            payload[1],
            digest_value(payload[0]),
            clock["rewrite_revision"],
            1,
            json.dumps(blocks, ensure_ascii=False, sort_keys=True),
        ),
    )


def load_artifact_classification(
    db: sqlite3.Connection, chat_id: str, session_id: str, kind: str
) -> tuple[str, int, list[dict[str, Any]]] | None:
    payload = _artifact_payload(db, chat_id, session_id, kind)
    if payload is None:
        return None
    row = db.execute(
        "SELECT v.payload_digest,v.through_rowid,v.blocks_json FROM memory_artifact_visibility v "
        "JOIN sessions s ON s.chat_id=v.chat_id AND s.session_id=v.session_id AND s.created_at=v.session_created_at "
        "WHERE v.chat_id=? AND v.session_id=? AND v.artifact_kind=? AND v.schema_version=1",
        (chat_id, session_id, kind),
    ).fetchone()
    if row is None or str(row[0]) != digest_value(payload[0]) or int(row[1]) != payload[1]:
        return None
    try:
        blocks = parse_classified_blocks(json.loads(row[2]))
    except (ValueError, TypeError):
        return None
    return str(row[0]), int(row[1]), blocks


def previous_classified_artifact(db: sqlite3.Connection, chat_id: str, session_id: str, kind: str) -> str:
    classification = load_artifact_classification(db, chat_id, session_id, kind)
    return json.dumps({"blocks": classification[2]}, ensure_ascii=False) if classification else ""


def read_artifact_block(
    db: sqlite3.Connection,
    scope: MemoryReadScope,
    kind: str,
    *,
    required_evidence: tuple[MemoryEvidence, ...] | None = None,
) -> MemoryBlock:
    row = db.execute(
        "SELECT created_at FROM sessions WHERE chat_id=? AND session_id=?",
        (scope.chat_id, scope.session_id),
    ).fetchone()
    if row is None or row[0] != scope.session_created_at:
        return MemoryBlock(channel=kind)
    classification = load_artifact_classification(db, scope.chat_id, scope.session_id, kind)
    if classification is None or classification[1] > request_source_cutoff(db, scope):
        return MemoryBlock(channel=kind)
    digest, through, blocks = classification
    evidence_digest = digest_value([digest, blocks])
    lines: list[str] = []
    evidence: list[MemoryEvidence] = []
    leaves: list[MemoryBlockLeaf] = []
    used = 0
    for index, item in enumerate(blocks):
        pointer = MemoryEvidence(
            source_end_rowid=through,
            artifact_kind=kind,
            artifact_digest=evidence_digest,
            block_index=index,
        )
        if required_evidence is not None and pointer not in required_evidence:
            continue
        if scope.consumer != "narrator" and (
            not scope.principals
            or (item["visibility"] != "shared" and not set(scope.principals).issubset(set(item["known_by"])))
        ):
            continue
        text = item["text"]
        if used + len(text) + bool(lines) > (SUMMARY_MAX_CHARS if kind == "summary" else 5000):
            continue
        used += len(text) + bool(lines)
        lines.append(text)
        evidence.append(pointer)
        leaves.append(
            MemoryBlockLeaf(text, pointer, scope, item["visibility"], tuple(item["known_by"]), scope.rewrite_revision)
        )
    return MemoryBlock("\n".join(lines), tuple(evidence), kind, tuple(leaves))


def read_summary_block(
    db: sqlite3.Connection,
    scope: MemoryReadScope,
    query: str = "",
    *,
    required_evidence: tuple[MemoryEvidence, ...] | None = None,
) -> MemoryBlock:
    """Rehydrate current and authorized historical windows within one cap."""
    from bridge.summary_archive_store import read_summary_archive

    current = read_artifact_block(db, scope, "summary", required_evidence=required_evidence)
    room = SUMMARY_MAX_CHARS - len(current.text) - bool(current.text)
    archived = read_summary_archive(
        db, scope, query, required_evidence=required_evidence, max_chars=min(6000, max(0, room))
    )
    return MemoryBlock(
        "\n".join(filter(None, (current.text, archived.text))),
        current.evidence + archived.evidence,
        "summary",
        current.leaves + archived.leaves,
    )


def read_scene_block(db: sqlite3.Connection, scope: MemoryReadScope) -> MemoryBlock:
    return read_artifact_block(db, scope, "scene")
