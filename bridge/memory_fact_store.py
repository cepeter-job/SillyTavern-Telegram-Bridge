"""Transactional native attestations, explicit events, and independent indexing."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from bridge.memory_contracts import MemoryEvidence, MemoryFact
from bridge.memory_store import MemorySource, enqueue_memory, external_memory_boundary, source_is_valid, store_segment
from bridge.narrative_repository import load_narrative_clock
from bridge.sqlite_store import write_transaction


def normalize_principals(names: Sequence[str]) -> tuple[str, ...]:
    return tuple(
        sorted({" ".join(name.split()).casefold() for name in names if isinstance(name, str) and name.strip()})
    )


def classified_audience(visibility: str, known_by: Sequence[str]) -> tuple[str, tuple[str, ...]]:
    if visibility not in {"shared", "restricted"} or not isinstance(known_by, (tuple, list)):
        raise ValueError("Memory classification must explicitly name shared or restricted visibility")
    if any(not isinstance(name, str) or not name.strip() for name in known_by):
        raise ValueError("Memory audience must contain character names")
    names = normalize_principals(known_by)
    if visibility == "restricted" and not names:
        raise ValueError("Restricted memory requires an identified character")
    if visibility == "shared" and names:
        raise ValueError("Shared memory cannot carry a conflicting restricted audience")
    return visibility, names


def digest_value(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def fact_payload_digest(fact: MemoryFact) -> str:
    return digest_value([fact.kind, fact.summary.strip(), fact.visibility, list(normalize_principals(fact.known_by))])


@dataclass(frozen=True)
class StoredMemoryFact:
    memory_id: int
    chat_id: str
    session_id: str
    session_created_at: float
    fact: MemoryFact
    evidence: MemoryEvidence
    payload_digest: str
    provenance_kind: str
    accepted_after_rowid: int = 0
    external_epoch: int = 0


def load_source(db: sqlite3.Connection, document_id: str) -> MemorySource | None:
    row = db.execute(
        "SELECT g.document_id,g.chat_id,g.session_id,g.session_created_at,g.layer,g.start_id,g.end_id,"
        "g.start_offset,g.end_offset,g.source_digest,g.rewrite_identity,g.purge_epoch,m.role,"
        "substr(m.content,g.start_offset+1,g.end_offset-g.start_offset) "
        "FROM memory_segments g JOIN messages m ON m.chat_id=g.chat_id AND m.session_id=g.session_id "
        "AND m.id=g.start_id WHERE g.document_id=? AND g.valid=1",
        (document_id,),
    ).fetchone()
    return MemorySource(*row) if row else None


def load_current_fact(db: sqlite3.Connection, memory_id: int) -> StoredMemoryFact | None:
    row = db.execute(
        "SELECT e.chat_id,e.session_id,p.session_created_at,e.kind,e.importance,e.summary,"
        "v.visibility,v.known_by_json,p.source_document_id,p.explicit_event_id,p.payload_digest,p.provenance_kind "
        "FROM episodic_memories e JOIN memory_fact_provenance p ON p.memory_id=e.memory_id AND p.valid=1 "
        "JOIN episodic_memory_visibility v ON v.memory_id=e.memory_id "
        "AND v.chat_id=e.chat_id AND v.session_id=e.session_id "
        "JOIN sessions s ON s.chat_id=e.chat_id AND s.session_id=e.session_id AND s.created_at=p.session_created_at "
        "WHERE e.memory_id=? AND p.chat_id=e.chat_id AND p.session_id=e.session_id",
        (memory_id,),
    ).fetchone()
    if row is None:
        return None
    try:
        audience = json.loads(row[7])
        visibility, names = classified_audience(row[6], audience)
    except (ValueError, TypeError):
        return None
    fact = MemoryFact(str(row[3]), float(row[4]), str(row[5]), visibility, names)
    if fact_payload_digest(fact) != row[10]:
        return None
    accepted_after = epoch = 0
    if row[11] == "transcript_part":
        source = load_source(db, str(row[8]))
        if (
            source is None
            or (source.chat_id, source.session_id, source.session_created_at) != tuple(row[:3])
            or not source_is_valid(db, source)
        ):
            return None
        evidence = MemoryEvidence(
            memory_id, source.document_id, source.start_id, source.end_id, source.start_offset, source.end_offset
        )
    elif row[11] == "explicit":
        event = db.execute(
            "SELECT accepted_after_rowid,external_epoch FROM memory_explicit_events "
            "WHERE event_id=? AND chat_id=? AND session_id=? AND session_created_at=? AND valid=1",
            (row[9], *row[:3]),
        ).fetchone()
        if event is None:
            return None
        accepted_after, epoch = int(event[0]), int(event[1])
        evidence = MemoryEvidence(memory_id=memory_id, explicit_event_id=int(row[9]))
    else:
        return None
    return StoredMemoryFact(
        memory_id,
        str(row[0]),
        str(row[1]),
        float(row[2]),
        fact,
        evidence,
        str(row[10]),
        str(row[11]),
        accepted_after,
        epoch,
    )


def _enqueue_fact_index(db: sqlite3.Connection, memory_id: int, attestation_key: str) -> None:
    stored = load_current_fact(db, memory_id)
    if stored is None:
        return
    boundary = external_memory_boundary(db, stored.chat_id, stored.session_id)
    if boundary is None:
        return
    if stored.provenance_kind == "transcript_part":
        if stored.evidence.source_start_rowid <= boundary["source_floor_id"]:
            return
    elif stored.external_epoch != boundary["purge_epoch"]:
        return
    document_id = "session:" + stored.session_id + ":fact:" + digest_value([attestation_key, boundary["purge_epoch"]])
    result = db.execute(
        "INSERT OR IGNORE INTO memory_fact_index(document_id,memory_id,chat_id,session_id,session_created_at,"
        "external_epoch,payload_digest,created_at) VALUES(?,?,?,?,?,?,?,?)",
        (
            document_id,
            memory_id,
            stored.chat_id,
            stored.session_id,
            stored.session_created_at,
            boundary["purge_epoch"],
            stored.payload_digest,
            time.time(),
        ),
    )
    if result.rowcount:
        enqueue_memory(db, stored.chat_id, stored.session_id, "hindsight")


def _insert_fact(
    db: sqlite3.Connection,
    chat_id: str,
    session_id: str,
    incarnation: float,
    fact: MemoryFact,
    *,
    source: MemorySource | None = None,
    event_id: int | None = None,
) -> int:
    visibility, names = classified_audience(fact.visibility, fact.known_by)
    if not fact.summary.strip():
        raise ValueError("Memory fact text is required")
    fact = MemoryFact(fact.kind, fact.importance, fact.summary.strip(), visibility, names)
    source_key = source.document_id if source else f"explicit:{event_id}"
    key = digest_value(
        [
            chat_id,
            session_id,
            incarnation,
            source_key,
            fact.kind,
            " ".join(fact.summary.split()).casefold(),
            visibility,
            list(names),
        ]
    )
    existing = db.execute("SELECT memory_id FROM memory_fact_provenance WHERE attestation_key=?", (key,)).fetchone()
    if existing:
        return int(existing[0])
    row = db.execute(
        "INSERT INTO episodic_memories(chat_id,session_id,kind,importance,summary,source_start_rowid,"
        "source_end_rowid,created_at) VALUES(?,?,?,?,?,?,?,?)",
        (
            chat_id,
            session_id,
            fact.kind,
            fact.importance,
            fact.summary,
            source.start_id if source else 0,
            source.end_id if source else 0,
            time.time(),
        ),
    )
    memory_id = int(row.lastrowid or 0)
    db.execute(
        "INSERT INTO episodic_memory_visibility VALUES(?,?,?,?,?)",
        (memory_id, chat_id, session_id, visibility, json.dumps(names, ensure_ascii=False)),
    )
    db.execute(
        "INSERT INTO memory_fact_provenance(memory_id,chat_id,session_id,session_created_at,provenance_kind,"
        "source_document_id,explicit_event_id,attestation_key,payload_digest) VALUES(?,?,?,?,?,?,?,?,?)",
        (
            memory_id,
            chat_id,
            session_id,
            incarnation,
            "transcript_part" if source else "explicit",
            source.document_id if source else None,
            event_id,
            key,
            fact_payload_digest(fact),
        ),
    )
    _enqueue_fact_index(db, memory_id, key)
    return memory_id


def accept_source_facts(
    db: sqlite3.Connection,
    source: MemorySource,
    facts: Sequence[MemoryFact],
    *,
    source_valid: Callable[[], bool] | None = None,
    advance_coverage: bool = True,
) -> tuple[int, ...] | None:
    """Accept a successful source part, including an empty extraction, atomically."""
    with write_transaction(db):
        if source.layer != "episodes" or not source_is_valid(db, source) or (source_valid and not source_valid()):
            return None
        if not store_segment(db, source, advance_coverage=advance_coverage):
            return None
        return tuple(
            _insert_fact(db, source.chat_id, source.session_id, source.session_created_at, fact, source=source)
            for fact in facts
        )


def remember_local_fact(db: sqlite3.Connection, chat_id: str, session_id: str, character_name: str, text: str) -> int:
    names = normalize_principals((character_name,))
    if not names:
        raise ValueError("Select an active character before remembering a fact")
    if not text.strip():
        raise ValueError("Enter a fact to remember")
    with write_transaction(db):
        clock = load_narrative_clock(db, chat_id, session_id)
        if clock is None:
            raise ValueError("The selected story session no longer exists")
        boundary = external_memory_boundary(db, chat_id, session_id)
        if boundary is None:
            enqueue_memory(db, chat_id, session_id, "hindsight")
            boundary = external_memory_boundary(db, chat_id, session_id)
        if boundary is None:
            raise ValueError("The selected story memory boundary is unavailable")
        text = text.strip()[:4000]
        key = digest_value(
            [
                chat_id,
                session_id,
                clock["session_created_at"],
                names[0],
                " ".join(text.split()).casefold(),
                clock["latest_rowid"],
                clock["rewrite_revision"],
                boundary["purge_epoch"],
            ]
        )
        db.execute(
            "INSERT OR IGNORE INTO memory_explicit_events(chat_id,session_id,session_created_at,principal,fact,"
            "accepted_after_rowid,external_epoch,event_key,accepted_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (
                chat_id,
                session_id,
                clock["session_created_at"],
                names[0],
                text,
                clock["latest_rowid"],
                boundary["purge_epoch"],
                key,
                time.time(),
            ),
        )
        event = db.execute(
            "SELECT event_id FROM memory_explicit_events WHERE event_key=? AND valid=1", (key,)
        ).fetchone()
        if event is None:
            raise ValueError("This memory assertion was invalidated; issue a new story assertion")
        return _insert_fact(
            db,
            chat_id,
            session_id,
            float(clock["session_created_at"]),
            MemoryFact("fact", 1.0, text, "restricted", names),
            event_id=int(event[0]),
        )


def index_fact_is_current(db: sqlite3.Connection, document_id: str) -> StoredMemoryFact | None:
    row = db.execute(
        "SELECT memory_id,external_epoch,payload_digest,chat_id,session_id,session_created_at "
        "FROM memory_fact_index WHERE document_id=? AND state IN ('pending','retained')",
        (document_id,),
    ).fetchone()
    if (
        row is None
        or db.execute(
            "SELECT 1 FROM memory_retired_documents WHERE document_id=?",
            (document_id,),
        ).fetchone()
    ):
        return None
    stored = load_current_fact(db, int(row[0]))
    boundary = external_memory_boundary(db, str(row[3]), str(row[4]))
    if (
        stored is None
        or boundary is None
        or row[1] != boundary["purge_epoch"]
        or row[5] != boundary["session_created_at"]
        or row[2] != stored.payload_digest
        or (stored.chat_id, stored.session_id, stored.session_created_at) != tuple(row[3:6])
        or (
            stored.provenance_kind == "transcript_part"
            and stored.evidence.source_start_rowid <= boundary["source_floor_id"]
        )
    ):
        return None
    return stored


def restore_snapshot_fact(db: sqlite3.Connection, chat_id: str, session_id: str, item: dict[str, Any]) -> None:
    """Rebuild copied attestation authority from the target's own canonical source."""
    if not db.in_transaction:
        raise RuntimeError("Snapshot provenance requires the caller's restoration transaction")
    provenance = item.get("provenance") or {}
    clock = load_narrative_clock(db, chat_id, session_id)
    if clock is None:
        raise ValueError("Missing target story incarnation")
    visibility, names = classified_audience(item["visibility"], json.loads(item["known_by_json"]))
    fact = MemoryFact(item["kind"], float(item["importance"]), item["summary"], visibility, names)
    if provenance.get("kind") == "transcript_part":
        rowid = int(provenance["source_start_rowid"])
        start, end = int(provenance["start_offset"]), int(provenance["end_offset"])
        row = db.execute(
            "SELECT role,content FROM messages WHERE chat_id=? AND session_id=? AND id=?",
            (chat_id, session_id, rowid),
        ).fetchone()
        if row is None or start < 0 or end < start or end > len(row[1]):
            return
        digest = digest_value([row[0], row[1][start:end]])
        if digest != provenance["source_digest"]:
            return
        enqueue_memory(db, chat_id, session_id, "episodes")
        state = db.execute(
            "SELECT rewrite_identity,purge_epoch FROM memory_layer_state WHERE chat_id=? AND session_id=? "
            "AND session_created_at=? AND layer='episodes'",
            (chat_id, session_id, clock["session_created_at"]),
        ).fetchone()
        document_id = (
            "session:"
            + session_id
            + ":source:"
            + digest_value(
                [
                    chat_id,
                    session_id,
                    clock["session_created_at"],
                    "episodes",
                    rowid,
                    start,
                    end,
                    digest,
                    *state,
                ]
            )
        )
        source = MemorySource(
            document_id,
            chat_id,
            session_id,
            float(clock["session_created_at"]),
            "episodes",
            rowid,
            rowid,
            start,
            end,
            digest,
            int(state[0]),
            int(state[1]),
            str(row[0]),
            str(row[1][start:end]),
        )
        accept_source_facts(db, source, [fact], advance_coverage=False)
    elif provenance.get("kind") == "explicit" and visibility == "restricted" and len(names) == 1:
        anchor = int(provenance["accepted_after_rowid"])
        if (
            anchor < 0
            or anchor >= int(clock["latest_rowid"])
            or (
                anchor
                and not db.execute(
                    "SELECT 1 FROM messages WHERE chat_id=? AND session_id=? AND id=?",
                    (chat_id, session_id, anchor),
                ).fetchone()
            )
        ):
            return
        enqueue_memory(db, chat_id, session_id, "hindsight")
        key = digest_value(
            [chat_id, session_id, clock["session_created_at"], "copied explicit", anchor, names, fact.summary]
        )
        db.execute(
            "INSERT OR IGNORE INTO memory_explicit_events(chat_id,session_id,session_created_at,principal,fact,"
            "accepted_after_rowid,external_epoch,event_key,accepted_at) VALUES(?,?,?,?,?,?,0,?,?)",
            (chat_id, session_id, clock["session_created_at"], names[0], fact.summary, anchor, key, time.time()),
        )
        event = db.execute(
            "SELECT event_id FROM memory_explicit_events WHERE event_key=? AND valid=1", (key,)
        ).fetchone()
        if event:
            _insert_fact(db, chat_id, session_id, float(clock["session_created_at"]), fact, event_id=int(event[0]))
