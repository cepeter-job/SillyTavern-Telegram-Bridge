"""Test-only setup and observation of persisted state; never imported by runtime."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from bridge.character_quality import _file_signature, _store_rank_state
from bridge.episodic_memory import _decode_known_by
from bridge.npc_repository import _normalize_name, list_npc_entities
from bridge.settings import AppSettings


def seed_character_rank(db: sqlite3.Connection, filename: str, rank: str, *, app_settings: AppSettings) -> None:
    signature = _file_signature(filename, app_settings=app_settings)
    if signature is not None:
        _store_rank_state(db, filename, rank, signature)


def find_test_npc(db, chat_id, session_id, canonical_name):
    wanted = _normalize_name(canonical_name)
    matches = [
        item for item in list_npc_entities(db, chat_id, session_id) if _normalize_name(item.canonical_name) == wanted
    ]
    return matches[0] if len(matches) == 1 else None


@dataclass(frozen=True)
class EpisodicMemory:
    memory_id: int
    kind: str
    importance: float
    summary: str
    source_start_rowid: int
    source_end_rowid: int
    visibility: str = "shared"
    known_by: tuple[str, ...] = ()


def read_episodic_memories(
    db: sqlite3.Connection, chat_id: str, session_id: str, limit: int = 10
) -> list[EpisodicMemory]:
    rows = db.execute(
        """
        SELECT m.memory_id,m.kind,m.importance,m.summary,m.source_start_rowid,m.source_end_rowid,
               COALESCE(v.visibility,'shared'),COALESCE(v.known_by_json,'[]')
        FROM episodic_memories AS m
        LEFT JOIN episodic_memory_visibility AS v ON v.memory_id=m.memory_id
        WHERE m.chat_id=? AND m.session_id=?
        ORDER BY m.importance DESC, m.memory_id DESC
        LIMIT ?
        """,
        (chat_id, session_id, limit),
    ).fetchall()
    result = []
    for memory_id, kind, importance, summary, start, end, visibility, known_by_json in rows:
        result.append(
            EpisodicMemory(
                int(memory_id),
                str(kind),
                float(importance),
                str(summary),
                int(start),
                int(end),
                str(visibility),
                _decode_known_by(known_by_json),
            )
        )
    return result
