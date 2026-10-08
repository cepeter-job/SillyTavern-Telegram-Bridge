"""Optional local context selection; only explicit approved slices can change prompts."""

from __future__ import annotations

from dataclasses import replace
from typing import Literal

from bridge.memory_contracts import ContextBlockSelection as ContextBlockSelection
from bridge.memory_contracts import MemoryBlock, MemoryBlockLeaf, MemoryEvidence, MemoryReadScope
from bridge.settings import AppSettings

ContextSelectionMode = Literal["off", "shadow", "enabled"]
CONTEXT_SELECTION_SLICES = frozenset({"dedup", "history", "summary"})
CONTEXT_SELECTION_REASONS = frozenset(
    {
        "off",
        "not_approved",
        "invalid_scope",
        "incomplete_coverage",
        "pending_invalidation",
        "source_changed",
        "historical",
        "ambiguous",
        "no_savings",
        "selected",
    }
)


def context_selection_mode(app_settings: AppSettings) -> ContextSelectionMode:
    value = app_settings.environ.get("SILLYTAVERN_CONTEXT_SELECTION_MODE", "off").strip().casefold()
    if value in {"shadow", "enabled"}:
        return value  # type: ignore[return-value]
    return "off"


def context_slice_enabled(app_settings: AppSettings, slice_name: str) -> bool:
    approved = {
        name.strip().casefold()
        for name in app_settings.environ.get("SILLYTAVERN_CONTEXT_SELECTION_SLICES", "").split(",")
    }
    return (
        context_selection_mode(app_settings) == "enabled"
        and slice_name in CONTEXT_SELECTION_SLICES
        and slice_name in approved
    )


def _valid_scope(scope: MemoryReadScope) -> bool:
    return bool(
        scope.chat_id
        and scope.session_id
        and scope.session_created_at > 0
        and scope.through_rowid >= 0
        and scope.rewrite_revision >= 0
        and scope.consumer in {"character", "narrator"}
        and (scope.consumer == "narrator" or (scope.principals and all(scope.principals)))
    )


def _canonical_pointer(pointer: MemoryEvidence, scope: MemoryReadScope) -> MemoryEvidence | None:
    if pointer.artifact_kind:
        valid = (
            pointer.artifact_kind in {"summary", "scene"}
            and bool(pointer.artifact_digest)
            and pointer.block_index >= 0
            and 0 <= pointer.source_end_rowid <= scope.through_rowid
            and not pointer.memory_id
            and not pointer.explicit_event_id
            and not pointer.source_document_id
            and not pointer.source_start_rowid
            and not pointer.start_offset
            and not pointer.end_offset
        )
    elif pointer.explicit_event_id:
        valid = (
            pointer.memory_id > 0
            and 0 < pointer.explicit_event_id <= scope.explicit_event_cutoff
            and not pointer.source_document_id
            and not pointer.source_start_rowid
            and not pointer.source_end_rowid
            and not pointer.start_offset
            and not pointer.end_offset
            and not pointer.artifact_digest
            and not pointer.block_index
        )
    else:
        valid = (
            pointer.memory_id > 0
            and bool(pointer.source_document_id)
            and 0 < pointer.source_start_rowid <= pointer.source_end_rowid <= scope.through_rowid
            and 0 <= pointer.start_offset < pointer.end_offset
            and not pointer.artifact_digest
            and not pointer.block_index
        )
    return replace(pointer, document_id="") if valid else None


def _leaf_key(leaf: MemoryBlockLeaf, scope: MemoryReadScope) -> tuple | None:
    if leaf.scope != scope or leaf.rewrite_revision != scope.rewrite_revision or not leaf.text:
        return None
    if leaf.visibility == "shared":
        if leaf.known_by:
            return None
    elif leaf.visibility == "restricted":
        if not leaf.known_by or (scope.consumer != "narrator" and not set(scope.principals).issubset(leaf.known_by)):
            return None
    else:
        return None
    pointer = _canonical_pointer(leaf.evidence, scope)
    if pointer is None:
        return None
    return pointer, leaf.scope, leaf.rewrite_revision, leaf.visibility, leaf.known_by, leaf.text


def select_memory_blocks(scope: MemoryReadScope, blocks: tuple[MemoryBlock, ...]) -> ContextBlockSelection:
    """Remove identical mapped attestations only; no textual relevance or provenance inference."""
    count = sum(len(block.leaves) if block.leaves else bool(block.text) for block in blocks)
    if not _valid_scope(scope):
        return ContextBlockSelection(blocks, count, reason="invalid_scope")
    if scope.historical:
        return ContextBlockSelection(blocks, count, reason="historical")
    selected: list[MemoryBlock] = []
    seen: set[tuple] = set()
    removed = 0
    ambiguous = False
    for block in blocks:
        # Summaries and active scene records remain complete in this slice.
        if block.channel not in {"recall", "episodic"} or not block.text:
            selected.append(block)
            continue
        if (
            not block.leaves
            or "\n".join(leaf.text for leaf in block.leaves) != block.text
            or tuple(leaf.evidence for leaf in block.leaves) != block.evidence
        ):
            ambiguous = True
            selected.append(block)
            continue
        kept: list[MemoryBlockLeaf] = []
        for leaf in block.leaves:
            key = _leaf_key(leaf, scope)
            if key is None:
                ambiguous = True
            elif key in seen:
                removed += 1
                continue
            else:
                seen.add(key)
            kept.append(leaf)
        selected.append(
            MemoryBlock(
                "\n".join(leaf.text for leaf in kept), tuple(leaf.evidence for leaf in kept), block.channel, tuple(kept)
            )
        )
    reason = "selected" if removed else "ambiguous" if ambiguous else "no_savings"
    return ContextBlockSelection(tuple(selected), count - removed, removed, reason)
