"""Application service for conversation-memory orchestration."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Literal

from bridge.memory_contracts import MemoryPromptContext as MemoryPromptContext
from bridge.port_contracts import (
    PurgeSessionMemory,
    ReadSummaryState,
    ResolveMemoryScope,
    RetainSessionMemory,
    ScopedRead,
    ScopedRecall,
    ValidateMemoryBlocks,
)


@dataclass(frozen=True)
class MemoryService:
    """Coordinate memory through injected pure contracts and one captured reader scope."""

    resolve_scope: ResolveMemoryScope
    scoped_recall: ScopedRecall
    scoped_episodes: ScopedRecall
    scoped_summary: ScopedRead
    scoped_scene: ScopedRead
    validate_blocks: ValidateMemoryBlocks
    summary_state: ReadSummaryState
    retain_session: RetainSessionMemory
    purge_session_memory: PurgeSessionMemory

    def prompt_context(
        self,
        db: sqlite3.Connection,
        chat_id: str,
        session: dict[str, str],
        fields: dict[str, str],
        query: str,
        *,
        through_rowid: int | None = None,
        edited_user_rowid: int | None = None,
        principals: tuple[str, ...] | None = None,
        consumer: Literal["character", "narrator"] = "character",
        historical: bool | None = None,
    ) -> MemoryPromptContext:
        if edited_user_rowid is not None:
            through_rowid = max(0, edited_user_rowid - 1)
            historical = True
        scope = self.resolve_scope(
            db,
            chat_id,
            session,
            fields,
            through_rowid=through_rowid,
            principals=principals,
            consumer=consumer,
            historical=historical,
        )
        if scope is None:
            return MemoryPromptContext()
        # Capture local evidence before slow recall, then revalidate every returned pointer.
        episodes = self.scoped_episodes(db, scope, query)
        summary = self.scoped_summary(db, scope)
        scene = self.scoped_scene(db, scope)
        recall = self.scoped_recall(db, scope, query)
        recall, episodes, summary, scene = self.validate_blocks(db, scope, (recall, episodes, summary, scene))
        return MemoryPromptContext(
            recall.text,
            summary.text,
            episodes.text,
            scene.text,
            scope,
            tuple(pointer for block in (recall, episodes, summary, scene) for pointer in block.evidence),
        )

    def summary_status(
        self,
        db: sqlite3.Connection,
        chat_id: str,
        session_id: str,
    ) -> tuple[str, int]:
        summary, covered_until = self.summary_state(
            db,
            chat_id,
            session_id,
        )
        return str(summary or ""), int(covered_until)

    def retain(
        self,
        db: sqlite3.Connection,
        chat_id: str,
        session: dict[str, str],
        fields: dict[str, str],
    ) -> None:
        self.retain_session(db, chat_id, session, fields)

    def purge_session(
        self,
        db: sqlite3.Connection,
        chat_id: str,
        session_id: str,
    ) -> int:
        result = self.purge_session_memory(db, chat_id, session_id)
        return 0 if result is None else int(result)
