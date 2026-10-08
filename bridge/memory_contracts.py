"""Pure immutable values for source-backed story knowledge."""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal, Protocol


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
    rewrite_event_cutoff: int = 0


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
class MemoryBlockLeaf:
    """An exact emitted payload bound to its locally authorized source and reader."""

    text: str
    evidence: MemoryEvidence
    scope: MemoryReadScope | None = None
    visibility: str = ""
    known_by: tuple[str, ...] = ()
    rewrite_revision: int = -1


@dataclass(frozen=True)
class MemoryBlock:
    text: str = ""
    evidence: tuple[MemoryEvidence, ...] = ()
    channel: str = ""
    leaves: tuple[MemoryBlockLeaf, ...] = ()


@dataclass(frozen=True)
class ContextBlockSelection:
    """Candidate payloads retain the baseline channel order and tuple arity."""

    blocks: tuple[MemoryBlock, ...]
    selected_blocks: int
    deduplicated_blocks: int = 0
    reason: str = "no_savings"


@dataclass(frozen=True)
class MemorySearchResult:
    """A locally rehydrated native fact selected by an optional remote identity."""

    document_id: str
    text: str
    type: str


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
    baseline_blocks: tuple[MemoryBlock, ...] = ()
    selection: ContextBlockSelection | None = None
    selection_mode: str = "off"
    selection_reason: str = "off"
    selection_coverage_valid: bool = False
    selection_guard: Callable[[], str] | None = field(default=None, compare=False, repr=False)


class SelectMemoryContext(Protocol):
    def __call__(
        self,
        db: sqlite3.Connection,
        context: MemoryPromptContext,
        resolve_current_scope: Callable[[], MemoryReadScope | None],
        /,
    ) -> MemoryPromptContext: ...


QUERY_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "at",
        "for",
        "from",
        "has",
        "have",
        "how",
        "in",
        "into",
        "is",
        "it",
        "its",
        "of",
        "on",
        "that",
        "the",
        "this",
        "to",
        "was",
        "were",
        "what",
        "when",
        "where",
        "who",
        "why",
        "with",
        "you",
        "your",
    }
)


def ordered_relevance_terms(value: str, *, limit: int = 24) -> list[str]:
    """Use bounded Unicode lexical terms; never interpret user input as FTS syntax."""
    terms = dict.fromkeys(
        token[:64]
        for token in re.findall(r"[^\W_]+(?:['-][^\W_]+)*", str(value or "")[:4096].casefold())
        if len(token) >= 2 and token not in QUERY_STOPWORDS
    )
    return list(terms)[: max(0, limit)]


def relevance_terms(value: str) -> set[str]:
    return set(ordered_relevance_terms(value, limit=128))


def expand_memory_query(query: str, principals: tuple[str, ...], scene: str) -> str:
    """Expand from already authorized scene blocks and resolved active readers only."""
    terms = ordered_relevance_terms(query, limit=12)
    terms.extend(ordered_relevance_terms(" ".join(principals[:8]), limit=6))
    terms.extend(ordered_relevance_terms(scene, limit=24))
    return " ".join(list(dict.fromkeys(terms))[:24])
