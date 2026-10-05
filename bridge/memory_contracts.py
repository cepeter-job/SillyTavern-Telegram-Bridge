"""Pure immutable values for source-backed story knowledge."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class MemoryReadScope:
    chat_id: str
    session_id: str
    session_created_at: float
    through_rowid: int
    rewrite_revision: int
    principals: tuple[str, ...]
    consumer: Literal["character", "narrator"] = "character"
    historical: bool = False
    explicit_event_cutoff: int = 0
    external_epoch: int = 0


@dataclass(frozen=True)
class MemoryEvidence:
    """Local pointers; a source or remote document alone grants no knowledge."""

    memory_id: int = 0
    source_document_id: str = ""
    source_start_rowid: int = 0
    source_end_rowid: int = 0
    start_offset: int = 0
    end_offset: int = 0
    explicit_event_id: int = 0
    artifact_kind: str = ""
    artifact_digest: str = ""
    block_index: int = 0
    document_id: str = ""


@dataclass(frozen=True)
class MemoryBlock:
    text: str = ""
    evidence: tuple[MemoryEvidence, ...] = ()
    channel: str = ""


@dataclass(frozen=True)
class MemoryFact:
    kind: str
    importance: float
    summary: str
    visibility: str
    known_by: tuple[str, ...] = ()


@dataclass(frozen=True)
class MemoryPromptContext:
    recall: str = ""
    summary: str = ""
    episodic: str = ""
    scene: str = ""
    scope: MemoryReadScope | None = None
    evidence: tuple[MemoryEvidence, ...] = ()
